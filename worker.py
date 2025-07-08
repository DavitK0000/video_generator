from PyQt5.QtCore import QThread, pyqtSignal
from utils import OpenAIHelper, create_output_directory, sanitize_for_script, split_text_into_chunks, get_first_paragraph, split_text_into_chunks_image, title_to_safe_folder_name, write_srt
from logging import Logger
import os, shutil, subprocess, random, math, traceback, requests, base64
import time
import gc  # Add garbage collection
import threading
from contextlib import contextmanager
from typing import Optional, Dict, List,Tuple
from dotenv import load_dotenv
import sys

# Load environment variables
load_dotenv()

POOL_SIZE = 10
TRANSCRIPTION_API_URL = "http://localhost:8080"

def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    # Try multiple possible locations for the resource file
    possible_paths = []
    
    # 1. Directory where the executable is located (for PyInstaller exe)
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller executable
        exe_dir = os.path.dirname(sys.executable)
        possible_paths.append(os.path.join(exe_dir, relative_path))
    
    # 2. PyInstaller bundle path (internal temp folder)
    try:
        if hasattr(sys, '_MEIPASS'):  # type: ignore
            possible_paths.append(os.path.join(sys._MEIPASS, relative_path))  # type: ignore
    except Exception:
        pass
    
    # 3. Current working directory
    possible_paths.append(os.path.join(os.getcwd(), relative_path))
    
    # 4. Script directory (for development)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths.append(os.path.join(script_dir, relative_path))
    
    # 5. Parent directory of script
    possible_paths.append(os.path.join(os.path.dirname(script_dir), relative_path))
    
    # 6. Common relative paths
    possible_paths.extend([
        os.path.join(".", relative_path),
        os.path.join("..", relative_path),
        relative_path  # Try as absolute path if it already is
    ])
    
    # Return the first path that exists
    for path in possible_paths:
        if os.path.exists(path):
            return os.path.abspath(path)
    
    # If no path exists, return the first candidate and let the error occur
    # This will provide a clear error message about what file is missing
    return os.path.abspath(possible_paths[0]) if possible_paths else relative_path

def create_temp_dir() -> str:
    """Create a temporary directory for processing"""
    # Determine the correct base directory
    if getattr(sys, 'frozen', False):
        # Running as PyInstaller executable - use directory containing the executable
        base_dir = os.path.dirname(sys.executable)
    else:
        # Running as script - use directory containing the script
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Create a unique temp directory for each worker to avoid conflicts
    import uuid
    temp_dir = os.path.join(base_dir, f"__temp___{uuid.uuid4().hex[:8]}")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir)
    os.makedirs(temp_dir)
    return temp_dir

def cleanup_temp_dir(temp_dir: str):
    """Clean up temporary directory"""
    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
    except Exception as e:
        print(f"Warning: Failed to cleanup temp directory: {e}")

def cleanup_all_temp_dirs():
    """Clean up all temporary directories (for application shutdown)"""
    try:
        # Determine the correct base directory
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Find and clean up all temp directories
        for item in os.listdir(base_dir):
            if item.startswith("__temp__"):
                temp_path = os.path.join(base_dir, item)
                if os.path.isdir(temp_path):
                    try:
                        shutil.rmtree(temp_path)
                    except Exception as e:
                        print(f"Warning: Failed to cleanup temp directory {temp_path}: {e}")
    except Exception as e:
        print(f"Warning: Failed to cleanup temp directories: {e}")

class BaseWorker(QThread):
    """Base class for worker threads with common functionality"""
    
    def __init__(self, logger: Logger):
        super().__init__()
        self.logger = logger
        self._is_cancelled = False
        self.start_time = None
        self.step_times: Dict[str, float] = {}
        self.active_processes: List[subprocess.Popen] = []
        self.process_lock = threading.Lock()

    def cancel(self):
        """Allow cancellation of the worker thread"""
        self._is_cancelled = True
        self._cleanup_processes()
        self.quit()

    def _cleanup_processes(self):
        """Clean up all active processes"""
        with self.process_lock:
            processes_to_cleanup = self.active_processes.copy()
            self.active_processes.clear()
        
        for process in processes_to_cleanup:
            try:
                if process.poll() is None:  # Process is still running
                    self.logger.info(f"Terminating subprocess PID {process.pid}")
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.logger.warning(f"Force killing subprocess PID {process.pid}")
                        process.kill()
                        process.wait()
            except Exception as e:
                self.logger.warning(f"Error cleaning up process: {e}")
    
    def _check_system_resources(self):
        """Check system resources and log warnings if low"""
        try:
            import psutil
            memory = psutil.virtual_memory()
            if memory.percent > 85:
                self.logger.warning(f"High memory usage: {memory.percent}%")
            
            cpu_percent = psutil.cpu_percent(interval=1)
            if cpu_percent > 90:
                self.logger.warning(f"High CPU usage: {cpu_percent}%")
                
        except ImportError:
            pass  # psutil not available
        except Exception as e:
            self.logger.warning(f"Error checking system resources: {e}")

    def _check_cancelled(self):
        """Check if operation was cancelled"""
        if self._is_cancelled:
            self._cleanup_processes()
            raise Exception("Operation cancelled by user")

    @contextmanager
    def _step_timer(self, step_name: str):
        """Context manager to time a step and log its duration"""
        start_time = time.time()
        try:
            yield
        finally:
            duration = time.time() - start_time
            self.step_times[step_name] = duration
            self.logger.info(f"⏱️ {step_name} completed in {duration:.2f} seconds")

    def _format_duration(self, seconds: float) -> str:
        """Format duration in a human-readable format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        
        if hours > 0:
            return f"{hours}h {minutes}m {secs:.1f}s"
        elif minutes > 0:
            return f"{minutes}m {secs:.1f}s"
        else:
            return f"{secs:.1f}s"

    def _safe_subprocess_run(self, cmd: List[str], timeout: int = 300, **kwargs) -> subprocess.CompletedProcess:
        """Wrapper for subprocess calls with timeout and error handling (sync)"""
        try:
            self._check_cancelled()
            self.logger.info(f"Running command: {' '.join(cmd[:3])}...")
            subprocess_kwargs = {
                'check': True,
                'timeout': timeout,
                'stdout': subprocess.PIPE,
                'stderr': subprocess.PIPE,
                'text': True
            }
            subprocess_kwargs.update(kwargs)
            return subprocess.run(cmd, **subprocess_kwargs)
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out after {timeout}s")
            raise Exception(f"FFmpeg operation timed out after {timeout} seconds")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed with exit code {e.returncode}")
            self.logger.error(f"stderr: {e.stderr}")
            raise Exception(f"FFmpeg operation failed: {e.stderr}")

    def _safe_requests_call(self, url: str, data: Optional[Dict] = None, timeout: int = 300, max_retries: int = 3) -> Dict:
        """Safe wrapper for requests with proper session management (sync)"""
        session = None
        try:
            for attempt in range(max_retries):
                try:
                    self._check_cancelled()
                    session = requests.Session()
                    session.headers.update({
                        'Connection': 'close',
                        'Content-Type': 'application/json'
                    })
                    response = session.post(url, json=data, timeout=timeout)
                    response.raise_for_status()
                    result = response.json()
                    if not result:
                        raise Exception("Empty response from server")
                    return result
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                    self.logger.warning(f"Request failed, attempt {attempt + 1}/{max_retries}: {e}")
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(2 ** attempt)
                finally:
                    if session:
                        session.close()
        except Exception as e:
            self.logger.error(f"Request failed after {max_retries} attempts: {e}")
            raise
        finally:
            if session:
                session.close()
        return {}

    def _log_runtime_summary(self):
        """Log a comprehensive summary of runtime statistics"""
        if self.start_time is None:
            self.logger.warning("Cannot log runtime summary: start_time is None")
            return
        total_runtime = time.time() - self.start_time
        
        self.logger.info("=" * 60)
        self.logger.info("🎬 VIDEO GENERATION RUNTIME SUMMARY")
        self.logger.info("=" * 60)
                
        self.logger.info(f"📊 TOTAL RUNTIME: {self._format_duration(total_runtime)}")
        self.logger.info("-" * 40)
        
        step_order = [
            "Initialization",
            "Script Generation", 
            "Thumbnail Generation",
            "Image Generation",
            "Audio Generation", 
            "Video Assembly"
        ]
        
        self.logger.info("📋 STEP-BY-STEP BREAKDOWN:")
        for step in step_order:
            if step in self.step_times:
                duration = self.step_times[step]
                percentage = (duration / total_runtime) * 100
                self.logger.info(f"   {step}: {self._format_duration(duration)} ({percentage:.1f}%)")
        
        self.logger.info("-" * 40)


class GenerationWorker(BaseWorker):
    progress_update = pyqtSignal(int)
    operation_update = pyqtSignal(str)
    generation_finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, api_key: str, video_title: str, background_music_path: str,
                 thumbnail_prompt: str, images_prompt: str,
                 intro_prompt: str, looping_prompt: str, outro_prompt: str,
                 loop_length: int, word_limit: int, image_count: int, image_word_limit: int,
                 runware_model: str, runware_loras: list, image_model: str, image_loras: list,
                 language: str, voice: str, logger: Logger, channel_name: str = "default"):
        super().__init__(logger)
        self.api_key = api_key
        self.video_title = video_title
        self.background_music_path = background_music_path
        self.thumbnail_prompt = thumbnail_prompt
        self.images_prompt = images_prompt
        self.intro_prompt = intro_prompt
        self.looping_prompt = looping_prompt
        self.outro_prompt = outro_prompt
        self.loop_length = loop_length
        self.word_limit = word_limit
        self.image_count = image_count
        self.image_word_limit = image_word_limit
        self.runware_model = runware_model or "runware:100@1"  # Default if not provided
        self.runware_loras = runware_loras or []  # Default to empty list if not provided
        self.image_model = image_model or "runware:100@1"  # Default if not provided
        self.image_loras = image_loras or []  # Default to empty list if not provided
        self.language = language or "a"  # Default to American English if not provided
        self.voice = voice or "am_michael"  # Default to Michael voice if not provided
        self.channel_name = channel_name or "default"  # Default channel name if not provided
        self.temp_dir = ""
        
        # Audio generation tracking
        self.audio_progress_lock = threading.Lock()
        self.completed_audio_count = 0
        self.total_audio_chunks = 0
        
        # Resource management - limit concurrent FFmpeg processes
        self.active_processes = []  # Track active processes for cleanup
        self.process_lock = threading.Lock()
        
        # Find FFmpeg path
        self.ffmpeg_path = self._find_ffmpeg()
        self.ffprobe_path = self._find_ffprobe()
        if not self.ffmpeg_path or not self.ffprobe_path:
            raise Exception("FFmpeg and/or FFprobe not found. Please ensure FFmpeg is installed and in the system PATH.")

    def _get_output_paths(self, output_dir: str) -> Dict[str, str]:
        """Get the correct paths for different file types in the new folder structure"""
        # output_dir is the final files directory (where final files go)
        # We need to get the parent directory to access the subdirectories
        main_video_dir = os.path.dirname(output_dir)  # This is the main video folder
        
        return {
            'main': output_dir,  # Final files (video, thumbnail, script)
            'images': os.path.join(main_video_dir, "images"),  # Generated images
            'voice_over': os.path.join(main_video_dir, "voice-over"),  # Audio files and subtitles
            'prompts': os.path.join(main_video_dir, "prompts"),  # Generated prompts
        }

    def _get_safe_video_title(self) -> str:
        """Get the safe video title for file naming"""
        from utils import title_to_safe_file_name
        return title_to_safe_file_name(self.video_title)

    def _find_ffmpeg(self) -> str:
        """Find the FFmpeg executable path"""
        # First check if ffmpeg is in the same directory as the script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ffmpeg_local = os.path.join(script_dir, 'ffmpeg.exe')
        if os.path.exists(ffmpeg_local):
            return ffmpeg_local
            
        # Then check common Windows installation paths
        common_paths = [
            os.path.join(script_dir, 'ffmpeg', 'bin', 'ffmpeg.exe'),
            r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
            r'C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe',
            r'C:\ffmpeg\bin\ffmpeg.exe'
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
                
        # Finally check if it's in PATH
        try:
            import shutil
            return shutil.which('ffmpeg') or ''
        except Exception:
            return ''

    def _find_ffprobe(self) -> str:
        """Find the FFprobe executable path"""
        # First check if ffprobe is in the same directory as the script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        ffprobe_local = os.path.join(script_dir, 'ffprobe.exe')
        if os.path.exists(ffprobe_local):
            return ffprobe_local
            
        # Then check common Windows installation paths
        common_paths = [
            os.path.join(script_dir, 'ffmpeg', 'bin', 'ffprobe.exe'),
            r'C:\Program Files\ffmpeg\bin\ffprobe.exe',
            r'C:\Program Files (x86)\ffmpeg\bin\ffprobe.exe',
            r'C:\ffmpeg\bin\ffprobe.exe'
        ]
        
        for path in common_paths:
            if os.path.exists(path):
                return path
                
        # Finally check if it's in PATH
        try:
            import shutil
            return shutil.which('ffprobe') or ''
        except Exception:
            return ''

    def _generate_single_audio(self, audio_task: Tuple[int, str, str]) -> Tuple[int, bool, Optional[str]]:
        """Generate a single audio file and get its transcription - includes retry polling"""
        idx, audio_chunk, output_dir = audio_task
        max_retries = 3
        
        # Get the correct paths for the new folder structure
        paths = self._get_output_paths(output_dir)
        voice_over_dir = paths['voice_over']
        
        for attempt in range(max_retries):
            try:
                self._check_cancelled()
                
                if attempt > 0:
                    self.logger.info(f"Retrying audio {idx + 1} generation, attempt {attempt + 1}/{max_retries}")
                    # Exponential backoff: 2^attempt seconds
                    time.sleep(2 ** attempt)
                
                # Get language and voice settings based on selected language
                tts_config = self._get_tts_config()
                
                data = {
                    'text': audio_chunk,
                    'voice': tts_config['voice'],
                    'speed': 1,
                    'language': tts_config['language']
                }
                
                result = self._safe_requests_call("http://localhost:8000/tts/base64", data, timeout=360)
                
                if 'audio_base64' not in result:
                    raise Exception("No audio data in TTS response")
                    
                audio_data = base64.b64decode(result['audio_base64'])
                
                # Save to file with correct naming in voice-over directory
                audio_filename = os.path.join(voice_over_dir, f"audio{idx+1}.wav")
                with open(audio_filename, 'wb') as f:
                    f.write(audio_data)

                # Get transcription for this audio file
                try:
                    # Run transcription request in executor
                    with open(audio_filename, 'rb') as f:
                        file_content = f.read()
                        files = {'file': (os.path.basename(audio_filename), file_content)}
                        response = requests.post(f"{TRANSCRIPTION_API_URL}/transcribe/", files=files)
                        
                    if response.status_code == 200:
                        result = response.json()
                        # Save individual SRT file in voice-over directory
                        with open(os.path.join(voice_over_dir, f"subtitle{idx+1}.srt"), 'w', encoding='utf-8') as f:
                            f.write(result['srt_content'])
                        self.logger.info(f"Generated transcription for audio {idx + 1}")
                    else:
                        self.logger.warning(f"Failed to get transcription for audio {idx + 1}: {response.text}")
                except Exception as e:
                    self.logger.warning(f"Transcription failed for audio {idx + 1}: {e}")
                
                # Thread-safe progress update
                with self.audio_progress_lock:
                    self.completed_audio_count += 1
                    progress = int(45 + (self.completed_audio_count / self.total_audio_chunks) * 20)
                    self.progress_update.emit(progress)
                
                if attempt > 0:
                    self.logger.info(f"✅ Audio {idx + 1} generated successfully on attempt {attempt + 1}")
                else:
                    self.logger.info(f"🎵 Generated audio {idx + 1} for chunk (parallel)")
                
                # Clear audio data and force garbage collection
                del audio_data
                gc.collect()
                
                return idx, True, None
                
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    self.logger.warning(f"Failed to generate audio {idx + 1} on attempt {attempt + 1}: {e}")
                else:
                    self.logger.error(f"Failed to generate audio {idx + 1} after {max_retries} attempts: {e}")
        
        # If all retries failed
        return idx, False, last_error

    def _generate_audio_parallel(self, audio_chunks: List[str], output_dir: str, max_workers: int = 4) -> bool:
        """Generate audio files in parallel with up to 4 concurrent threads"""
        self.logger.info(f"🎵 Starting parallel audio generation with {max_workers} workers")
        
        # Get the correct paths for the new folder structure
        paths = self._get_output_paths(output_dir)
        voice_over_dir = paths['voice_over']
        
        # Reset progress tracking
        with self.audio_progress_lock:
            self.completed_audio_count = 0
            self.total_audio_chunks = len(audio_chunks)
        
        # Create list of tasks (index, chunk, output_dir)
        audio_tasks = [(idx, chunk, output_dir) for idx, chunk in enumerate(audio_chunks)]
        
        # Track results to ensure all files are generated
        results = {}
        failed_tasks = []
        
        # Process tasks in batches with size limited to max_workers
        for i in range(0, len(audio_tasks), max_workers):
            batch = audio_tasks[i:i+max_workers]
            tasks = [self._generate_single_audio(task) for task in batch]
            completed_batch = [task for task in tasks if task[1] is True]
            
            # Process completed batch
            for task_result in completed_batch:
                if isinstance(task_result, Exception):
                    failed_tasks.append((-1, str(task_result)))  # Index unknown in this case
                else:
                    if isinstance(task_result, tuple) and len(task_result) == 3:
                        idx, success, error = task_result
                        results[idx] = success
                        if not success:
                            failed_tasks.append((idx, error))
                    else:
                        failed_tasks.append((-1, f"Unexpected result format: {task_result}"))
        
        if failed_tasks:
            failed_indices = [str(idx + 1) for idx, _ in failed_tasks if idx >= 0]
            raise Exception(f"Failed to generate audio files: {', '.join(failed_indices)}")
        
        # Verify all files exist
        missing_files = []
        for idx in range(len(audio_chunks)):
            filename = os.path.join(voice_over_dir, f"audio{idx+1}.wav")
            if not os.path.exists(filename):
                missing_files.append(f"audio{idx+1}.wav")
        
        if missing_files:
            raise Exception(f"Missing audio files after generation: {', '.join(missing_files)}")
        
        self.logger.info(f"✅ Successfully generated {len(audio_chunks)} audio files in parallel")
        gc.collect()
        return True

    def _get_language_instruction(self) -> str:
        """Get language instruction for script generation based on selected language"""
        language_instructions = {
            "a": "Please generate all content in American English.",
            "b": "Please generate all content in British English.",
            "e": "Por favor genera todo el contenido en Español.",
            "f": "Veuillez générer tout le contenu en français.",
            "h": "कृपया सभी सामग्री हिंदी में जेनरेट करें।",
            "i": "Si prega di generare tutti i contenuti in italiano.",
            "j": "すべてのコンテンツを日本語で生成してください。",
            "p": "Por favor, gere todo o conteúdo em português brasileiro.",
            "z": "请用中文生成所有内容。"
        }
        return language_instructions.get(self.language, language_instructions["a"])

    def _get_tts_config(self) -> Dict[str, str]:
        """Get TTS configuration (language and voice) based on selected language and voice"""
        return {
            "language": self.language,
            "voice": self.voice
        }

    def _get_subtitle_font(self) -> str:
        """Get appropriate font for subtitles based on language"""
        cjk_languages = ["z", "j"]  # Chinese and Japanese
        
        if self.language in cjk_languages:
            # For CJK languages, try common CJK fonts available on Windows
            cjk_fonts = [
                "SimSun",           # Chinese - most common on Windows
                "Microsoft YaHei",  # Chinese - modern UI font
                "SimHei",           # Chinese - sans-serif
                "MS Gothic",        # Japanese - common on Windows
                "Yu Gothic",        # Japanese - modern Windows font
                "Meiryo",           # Japanese - clear UI font
                "NotoSansCJK",      # Universal CJK font if installed
                "Arial Unicode MS", # Fallback with CJK support
                "DejaVu Sans"       # Final fallback
            ]
            return cjk_fonts[0]  # Return the first (most reliable) option
        else:
            return "Montserrat"  # Keep Montserrat for non-CJK languages

    def _generate_scripts(self, openai_helper: OpenAIHelper) -> Tuple[str, str, str]:
        """Generate intro, looping, and outro scripts"""
        with self._step_timer("Script Generation"):
            # Add language instruction to prompts
            language_instruction = self._get_language_instruction()
            # Generate intro script
            self.logger.info("Generating intro scripts....")
            intro_prompt_with_lang = f"{self.intro_prompt}\n\n{language_instruction}"
            result = self._safe_api_call(
                openai_helper.generate_text,
                prompt=intro_prompt_with_lang
            )
            if result is None:
                intro_script, prev_id = "", None
            else:
                intro_script, prev_id = result
            self.progress_update.emit(6)
            # Generate looping scripts
            looping_script = ""
            looping_prompt_with_lang = f"{self.looping_prompt}\n\n{language_instruction}"
            for idx in range(1, self.loop_length + 1):
                self._check_cancelled()
                self.logger.info(f"Generating looping scripts({idx}/{self.loop_length})....")
                result = self._safe_api_call(
                    openai_helper.generate_text,
                    prompt=looping_prompt_with_lang,
                    prev_id=prev_id
                )
                if result is None:
                    script, prev_id = "", None
                else:
                    script, prev_id = result
                looping_script += script + '\n\n'
                self.progress_update.emit(int(6 + idx / self.loop_length * 3))
                time.sleep(0.5)
            # Generate outro script
            self.logger.info("Generating outro scripts....")
            outro_prompt_with_lang = f"{self.outro_prompt}\n\n{language_instruction}"
            result = self._safe_api_call(
                openai_helper.generate_text,
                prompt=outro_prompt_with_lang,
                prev_id=prev_id
            )
            if result is None:
                outro_script, prev_id = "", None
            else:
                outro_script, prev_id = result
            self.progress_update.emit(10)
            return intro_script, looping_script, outro_script

    def _generate_thumbnail(self, output_dir: str, openai_helper: OpenAIHelper):
        """Generate thumbnail image using Runware - includes retry polling"""
        with self._step_timer("Thumbnail Generation"):
            max_retries = 3
            
            for attempt in range(max_retries):
                try:
                    self._check_cancelled()
                    
                    if attempt > 0:
                        self.logger.info(f"Retrying thumbnail generation, attempt {attempt + 1}/{max_retries}")
                        # Exponential backoff: 2^attempt seconds
                        time.sleep(2 ** attempt)
                    
                    # First generate the prompt using OpenAI
                    self.logger.info("Generating thumbnail prompt using OpenAI...")
                    result = self._safe_api_call(
                        openai_helper.generate_text,
                        prompt=self.thumbnail_prompt
                    )
                    if result is None:
                        generated_prompt, _ = "", None
                    else:
                        generated_prompt, _ = result
                    
                    # Get the correct paths for the new folder structure
                    paths = self._get_output_paths(output_dir)
                    prompts_dir = paths['prompts']
                    
                    # Save the generated prompt
                    with open(os.path.join(prompts_dir, 'thumbnail-prompt.txt'), 'w', encoding='utf-8') as f:
                        f.write(generated_prompt)
                    
                    # Generate image using local Runware service
                    generate_params = {
                        "positive_prompt": generated_prompt,
                        "model": self.runware_model,
                        "num_results": 1,
                        "height": 768,
                        "width": 1280,
                        "negative_prompt": "blurry, low quality, distorted"
                    }
                    
                    # Add Loras if provided
                    if self.runware_loras:
                        # Convert to the format expected by the local service
                        formatted_loras = []
                        for lora_dict in self.runware_loras:
                            formatted_loras.append({
                                "model": lora_dict["model"],
                                "weight": lora_dict["weight"]
                            })
                        generate_params["lora"] = formatted_loras
                    else:
                        generate_params["lora"] = None
                    
                    self.logger.info(f"Generating thumbnail with params: {generate_params}")
                    self.logger.info("Calling local Runware service...")
                    
                    # Call local Runware service
                    result = self._safe_requests_call("http://127.0.0.1:8088/generate-images", generate_params, timeout=360)
                    
                    if not result.get("success"):
                        raise Exception(f"Local Runware service failed: {result.get('message', 'Unknown error')}")
                    
                    images = result.get("images", [])
                    if not images:
                        raise Exception("No images were generated by local Runware service")
                    
                    self.logger.info("Successfully received image from local Runware service")
                    
                    # Download and save the first image
                    image_url = images[0]
                    self.logger.info(f"Downloading image from URL: {image_url}")
                    response = requests.get(image_url)
                    self.logger.info("Image download completed")
                    response.raise_for_status()
                    
                    # Save the original image data
                    image_data = response.content
                    
                    # Use PIL to resize to exactly 1280x720
                    self.logger.info(f"Resizing thumbnail to 1280x720 using PIL...")
                    try:
                        from PIL import Image
                        from io import BytesIO
                        
                        # Open the image from bytes
                        img = Image.open(BytesIO(image_data))
                        
                        # Get original dimensions
                        width, height = img.size
                        self.logger.info(f"Original image dimensions: {width}x{height}")
                        
                        # Calculate the aspect ratios
                        target_ratio = 1280 / 720
                        img_ratio = width / height
                        
                        # Resize and crop to fill 1280x720 completely
                        if img_ratio > target_ratio:
                            # Image is wider than target, resize based on height
                            new_height = 720
                            new_width = int(new_height * img_ratio)
                            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                            # Crop the sides to match target width
                            left = (new_width - 1280) // 2
                            img = img.crop((left, 0, left + 1280, 720))
                        else:
                            # Image is taller than target, resize based on width
                            new_width = 1280
                            new_height = int(new_width / img_ratio)
                            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                            # Crop the top/bottom to match target height
                            top = (new_height - 720) // 2
                            img = img.crop((0, top, 1280, top + 720))
                        
                        # Save the final image in main directory with proper filename
                        safe_title = self._get_safe_video_title()
                        thumbnail_path = os.path.join(output_dir, f'{safe_title}.jpg')
                        img.save(thumbnail_path, "JPEG", quality=95)
                        self.logger.info(f"Successfully saved thumbnail to: {thumbnail_path}")
                        
                    except ImportError:
                        self.logger.warning("PIL not available, saving original image...")
                        # If PIL is not available, save the original image
                        safe_title = self._get_safe_video_title()
                        thumbnail_path = os.path.join(output_dir, f'{safe_title}.jpg')
                        with open(thumbnail_path, 'wb') as f:
                            f.write(image_data)
                        self.logger.info(f"Successfully saved original image to: {thumbnail_path}")
                    
                    # If we reach here, the thumbnail was generated successfully
                    if attempt > 0:
                        self.logger.info(f"✅ Thumbnail generated successfully on attempt {attempt + 1}")
                    
                    # Rate limit protection: sleep 500ms after thumbnail generation
                    time.sleep(0.5)
                    
                    self.progress_update.emit(25)
                    gc.collect()
                    return  # Success, exit the retry loop
                    
                except Exception as e:
                    last_error = str(e)
                    if attempt < max_retries - 1:
                        self.logger.warning(f"Failed to generate thumbnail on attempt {attempt + 1}: {e}")
                    else:
                        self.logger.error(f"Failed to generate thumbnail after {max_retries} attempts: {e}")
            
            # If all retries failed
            raise Exception(f"Thumbnail generation failed after {max_retries} attempts: {last_error}")

    def _generate_single_image(self, image_task: Tuple[int, str, str, Dict]) -> Tuple[int, bool, Optional[str]]:
        """Generate a single image with Runware - includes retry polling"""
        idx, chunk, output_dir, generate_params = image_task
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                self._check_cancelled()
                
                if attempt > 0:
                    self.logger.info(f"Retrying image {idx + 1} generation, attempt {attempt + 1}/{max_retries}")
                    # Exponential backoff: 2^attempt seconds
                    time.sleep(2 ** attempt)
                
                # First generate the prompt using OpenAI
                chunk_prompt = self.images_prompt.replace('$chunk', chunk)
                self.logger.info(f"Generating image prompt {idx + 1} using OpenAI...")
                result = self._safe_api_call(
                    self.openai_helper.generate_text,
                    prompt=chunk_prompt
                )
                if result is None:
                    generated_prompt, _ = "", None
                else:
                    generated_prompt, _ = result
                
                # Get the correct paths for the new folder structure
                paths = self._get_output_paths(output_dir)
                prompts_dir = paths['prompts']
                
                # Save the generated prompt
                with open(os.path.join(prompts_dir, f"image{idx + 1}-prompt.txt"), 'w', encoding='utf-8') as f:
                    f.write(generated_prompt)
                
                # Update the prompt in generate params
                generate_params["positive_prompt"] = generated_prompt
                
                self.logger.info(f"Generating image {idx + 1} with params...")
                
                # Call local Runware service
                result = self._safe_requests_call("http://127.0.0.1:8088/generate-images", generate_params, timeout=360)
                
                if not result.get("success"):
                    raise Exception(f"Local Runware service failed: {result.get('message', 'Unknown error')}")
                
                images = result.get("images", [])
                if not images:
                    raise Exception("No images were generated by local Runware service")
                
                # Download and save the first image
                image_url = images[0]
                response = requests.get(image_url)
                response.raise_for_status()
                
                # Process and resize the image using PIL
                try:
                    from PIL import Image
                    from io import BytesIO
                    
                    # Open the image from bytes
                    img = Image.open(BytesIO(response.content))
                    
                    # Get original dimensions
                    width, height = img.size
                    
                    # Calculate the aspect ratios
                    target_ratio = 1920 / 1080
                    img_ratio = width / height
                    
                    # Resize and crop to fill target dimensions completely
                    if img_ratio > target_ratio:
                        # Image is wider than target, resize based on height
                        new_height = 1080
                        new_width = int(new_height * img_ratio)
                        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                        # Crop the sides to match target width
                        left = (new_width - 1920) // 2
                        img = img.crop((left, 0, left + 1920, 1080))
                    else:
                        # Image is taller than target, resize based on width
                        new_width = 1920
                        new_height = int(new_width / img_ratio)
                        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
                        # Crop the top/bottom to match target height
                        top = (new_height - 1080) // 2
                        img = img.crop((0, top, 1920, top + 1080))
                    
                    # Save the final image in images directory
                    images_dir = paths['images']
                    image_path = os.path.join(images_dir, f'image{idx + 1}.jpg')
                    img.save(image_path, "JPEG", quality=95)
                    
                except ImportError:
                    self.logger.warning("PIL not available, saving original image...")
                    # If PIL is not available, save the original image
                    images_dir = paths['images']
                    image_path = os.path.join(images_dir, f'image{idx + 1}.jpg')
                    with open(image_path, 'wb') as f:
                        f.write(response.content)
                
                # If we reach here, the image was generated successfully
                if attempt > 0:
                    self.logger.info(f"✅ Image {idx + 1} generated successfully on attempt {attempt + 1}")
                
                # Rate limit protection: sleep 500ms after image generation
                time.sleep(0.5)
                
                gc.collect()
                return idx, True, None
                
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    self.logger.warning(f"Failed to generate image {idx + 1} on attempt {attempt + 1}: {e}")
                else:
                    self.logger.error(f"Failed to generate image {idx + 1} after {max_retries} attempts: {e}")
        
        # If all retries failed
        return idx, False, last_error

    def _generate_images(self, total_script: str, output_dir: str, openai_helper: OpenAIHelper):
        """Generate images based on script in parallel"""
        with self._step_timer("Image Generation"):
            self.openai_helper = openai_helper  # Store for use in _generate_single_image
            
            image_chunks = split_text_into_chunks_image(
                total_script,
                chunks_count=self.image_count,
                word_limit=self.image_word_limit
            )
            
            self.logger.info(f"📝 Script length: {len(total_script)} characters")
            self.logger.info(f"🖼️ Generated {len(image_chunks)} image chunks (requested {self.image_count})")
            for i, chunk in enumerate(image_chunks[:3]):  # Log first 3 chunks for debugging
                self.logger.info(f"   Chunk {i+1}: {chunk[:100]}{'...' if len(chunk) > 100 else ''}")
            
            # Calculate dimensions that are multiples of 64
            target_width = 1920
            target_height = 1080
            runware_width = ((target_width + 63) // 64) * 64  # Round up to nearest multiple of 64
            runware_height = ((target_height + 63) // 64) * 64  # Round up to nearest multiple of 64
            
            # Base generate parameters
            generate_params = {
                "model": self.image_model,
                "num_results": 1,
                "height": runware_height,
                "width": runware_width,
                "negative_prompt": "blurry, low quality, distorted"
            }
            
            # Add Loras if provided
            if self.image_loras:
                # Convert to the format expected by the local service
                formatted_loras = []
                for lora_dict in self.image_loras:
                    formatted_loras.append({
                        "model": lora_dict["model"],
                        "weight": lora_dict["weight"]
                    })
                generate_params["lora"] = formatted_loras
            else:
                generate_params["lora"] = None
            
            # Create list of tasks (index, chunk, output_dir, generate_params)
            image_tasks = [(idx, chunk, output_dir, generate_params.copy()) 
                          for idx, chunk in enumerate(image_chunks)]
            
            # Track results to ensure all files are generated
            results = {}
            failed_tasks = []
            
            # Process tasks in batches with size limited to 4 workers
            max_workers = 4
            for i in range(0, len(image_tasks), max_workers):
                batch = image_tasks[i:i+max_workers]
                tasks = [self._generate_single_image(task) for task in batch]
                completed_batch = [task for task in tasks if task[1] is True]
                
                # Process completed batch
                for task_result in completed_batch:
                    if isinstance(task_result, Exception):
                        failed_tasks.append((-1, str(task_result)))  # Index unknown in this case
                    else:
                        if isinstance(task_result, tuple) and len(task_result) == 3:
                            idx, success, error = task_result
                            results[idx] = success
                            if not success:
                                failed_tasks.append((idx, error))
                        else:
                            failed_tasks.append((-1, f"Unexpected result format: {task_result}"))
                
                # Update progress
                progress = 25 + (min(i + len(batch), len(image_tasks)) / len(image_tasks) * 20)
                self.progress_update.emit(int(progress))
                
                # Small delay between batches
                time.sleep(1)
            
            if failed_tasks:
                failed_details = []
                for idx, error in failed_tasks:
                    if idx >= 0:
                        failed_details.append(f"Image {idx + 1}: {error}")
                    else:
                        failed_details.append(f"Unknown image: {error}")
                
                # Log detailed errors for debugging
                for detail in failed_details:
                    self.logger.error(f"Image generation failed - {detail}")
                
                failed_indices = [str(idx + 1) for idx, _ in failed_tasks if idx >= 0]
                raise Exception(f"Failed to generate images: {', '.join(failed_indices)}")
            
            # Get the correct paths for the new folder structure
            paths = self._get_output_paths(output_dir)
            images_dir = paths['images']
            
            # Verify all files exist
            missing_files = []
            for idx in range(len(image_chunks)):
                filename = os.path.join(images_dir, f"image{idx+1}.jpg")
                if not os.path.exists(filename):
                    missing_files.append(f"image{idx+1}.jpg")
            
            if missing_files:
                raise Exception(f"Missing image files after generation: {', '.join(missing_files)}")
            
            self.logger.info(f"✅ Successfully generated {len(image_chunks)} images in parallel")
            gc.collect()

    def _assemble_video(self, output_dir: str, audio_duration: float, num_images: int):
        """Assemble the final video with audio and effects"""
        with self._step_timer("Video Assembly"):
            # Check system resources before starting intensive video processing
            self._check_system_resources()
            
            # Create zoomed clips for each image
            zoom_clips = []
            for idx in range(1, num_images + 1):
                self._check_cancelled()
                
                # Check resources periodically during processing
                if idx % 3 == 0:  # Check every 3rd iteration
                    self._check_system_resources()
                    gc.collect()  # Force garbage collection
                
                # Get the correct paths for the new folder structure
                paths = self._get_output_paths(output_dir)
                images_dir = paths['images']
                img = os.path.join(images_dir, f"image{idx}.jpg")
                out_clip = os.path.join(self.temp_dir, f'zoom{idx}.mp4')
                zoom_clips.append(os.path.abspath(out_clip))

                # Verify input image exists
                if not os.path.exists(img):
                    raise Exception(f"Input image not found: {img}")

                speed = 0.001
                zoom_directions = [
                    f"scale=8000x4500, zoompan=z='zoom+{speed}':x='trunc(iw/2-(iw/zoom/2))':y='trunc(ih/2-(ih/zoom/2))':d=120:fps=30,scale=1920:1080",
                    f"scale=8000x4500, zoompan=z='zoom+{speed}':x='0':y='0':d=120:fps=30,scale=1920:1080",
                    f"scale=8000x4500, zoompan=z='zoom+{speed}':x='trunc(iw-(iw/zoom))':y='0':d=120:fps=30,scale=1920:1080",
                    f"scale=8000x4500, zoompan=z='zoom+{speed}':x='0':y='trunc(ih-(ih/zoom))':d=120:fps=30,scale=1920:1080",
                    f"scale=8000x4500, zoompan=z='zoom+{speed}':x='trunc(iw-(iw/zoom))':y='trunc(ih-(ih/zoom))':d=120:fps=30,scale=1920:1080",
                ]

                zoom_filter = random.choice(zoom_directions)
                
                self.logger.info(f"Processing image {idx}/{num_images} - Creating zoom effect...")
                self.logger.info(f"Using zoom filter: {zoom_filter}")
                
                if idx < num_images:
                    duration = 4  # zoom_duration
                    out_clip = os.path.join(self.temp_dir, f'zoom{idx}.mp4')
                    zoom_clips.append(os.path.abspath(out_clip))

                    # Verify input image exists
                    if not os.path.exists(img):
                        raise Exception(f"Input image not found: {img}")

                    cmd = [
                        'ffmpeg', '-y', '-loop', '1', '-i', os.path.abspath(img),
                        '-preset', 'ultrafast',
                        '-threads', '4',  # Reduced thread count to prevent resource exhaustion
                        '-vf', zoom_filter,
                        '-s', '1920x1080',
                        '-t', str(duration), '-pix_fmt', 'yuv420p', 
                        out_clip
                    ]
                    
                    # Try the command with a shorter timeout first
                    try:
                        self._safe_subprocess_run(cmd, timeout=180)  # Reduced timeout
                    except Exception as e:
                        if "timeout" in str(e).lower() or "zoompan" in str(e).lower():
                            self.logger.warning(f"Zoom effect failed, trying simpler approach for image {idx}")
                            # Use a much simpler approach without complex zoompan
                            simple_cmd = [
                                'ffmpeg', '-y', '-loop', '1', '-i', os.path.abspath(img),
                                '-preset', 'ultrafast',
                                '-threads', '2',
                                '-vf', 'scale=1920:1080',
                                '-t', str(duration), '-pix_fmt', 'yuv420p',
                                out_clip
                            ]
                            self._safe_subprocess_run(simple_cmd, timeout=60)
                        else:
                            raise
                else:
                    # Apply particle effect to the last image
                    particle_effect = os.path.join(self.temp_dir, 'last_with_particles.mp4')
                    extended_particle_effect = os.path.join(self.temp_dir, 'extended_last_with_particles.mp4')

                    particles_path = get_resource_path(os.path.join("reference", "particles.webm"))
                    self.logger.info(f"Looking for particles.webm at: {particles_path}")
                    
                    if not os.path.exists(particles_path):
                        # Log some debug information
                        self.logger.error(f"particles.webm not found at {particles_path}")
                        self.logger.info(f"Current working directory: {os.getcwd()}")
                        self.logger.info(f"Script directory: {os.path.dirname(os.path.abspath(__file__))}")
                        meipass = getattr(sys, '_MEIPASS', None)
                        if meipass is not None:
                            self.logger.info(f"PyInstaller temp path: {meipass}")
                        
                        # Try to find any particles.webm file
                        for root, dirs, files in os.walk(os.getcwd()):
                            if "particles.webm" in files:
                                self.logger.info(f"Found particles.webm at: {os.path.join(root, 'particles.webm')}")
                                break
                        else:
                            self.logger.error("particles.webm not found anywhere in current directory tree")
                        
                        raise Exception(f"particles.webm file not found. Expected at: {particles_path}")
                    
                    particle_loops = math.ceil(audio_duration / self._get_duration(particles_path))
                    
                    # Combine image with particle effect
                    cmd_particle = [
                        'ffmpeg', '-loop', '1', '-i', os.path.abspath(img), 
                        '-i', os.path.abspath(particles_path),
                        '-filter_complex', "[0:v]scale=1920:1080,setsar=1[bg];"
                        "[1:v]scale=1920:1080,format=rgba,colorchannelmixer=aa=0.3[particles];"
                        "[bg][particles]overlay=format=auto",
                        '-shortest', '-pix_fmt', 'yuv420p',
                        '-s', '1920x1080', "-y", particle_effect
                    ]
                    self._safe_subprocess_run(cmd_particle, timeout=450)

                    # Extend the particle effect video
                    cmd_extend = [
                        'ffmpeg', '-stream_loop', f'{str(particle_loops)}', '-i', particle_effect,
                        '-c', 'copy', extended_particle_effect
                    ]
                    self._safe_subprocess_run(cmd_extend, timeout=300)

                    zoom_clips[-1] = os.path.abspath(extended_particle_effect)

                # Verify output file was created
                if not os.path.exists(out_clip if idx < num_images else extended_particle_effect):
                    raise Exception(f"Failed to create video clip for image {idx}")

                self.progress_update.emit(int(65 + idx / num_images * 25))

            self.logger.info("Converting clips to transport stream format...")
            # Convert clips to transport stream format
            ts_clips = []
            for i, clip in enumerate(zoom_clips, 1):
                self._check_cancelled()
                self.logger.info(f"Converting clip {i}/{len(zoom_clips)} to transport stream...")
                ts_path = clip.replace(".mp4", ".ts")
                self._safe_subprocess_run([
                    "ffmpeg", "-y", "-i", clip,
                    "-c", "copy", "-bsf:v", "h264_mp4toannexb",
                    "-f", "mpegts", ts_path
                ], timeout=300)
                
                # Verify conversion
                if not os.path.exists(ts_path):
                    raise Exception(f"Failed to convert clip {i} to transport stream format")
                    
                ts_clips.append(ts_path)
                # Update progress for TS conversion
                self.progress_update.emit(int(90 + (i / len(zoom_clips)) * 5))

            self.logger.info("Concatenating video clips...")
            # Concatenate video clips
            full_video = os.path.join(self.temp_dir, 'slideshow.mp4')
            concat_input = '|'.join(ts_clips)
            self._safe_subprocess_run([
                "ffmpeg", "-y", "-i", f"concat:{concat_input}",
                "-c", "copy", "-bsf:a", "aac_adtstoasc", full_video
            ], timeout=1200)  # Increased timeout for concatenation
            self.progress_update.emit(95)  # Update progress after concatenation

            # Verify concatenation
            if not os.path.exists(full_video):
                raise Exception("Failed to concatenate video clips")

            self.logger.info("Combining video with audio and subtitles...")
            # Combine video with audio and subtitles
            paths = self._get_output_paths(output_dir)
            voice_over_dir = paths['voice_over']
            srt_path = os.path.join(voice_over_dir, "subtitle.srt")
            
            # Verify required files exist
            if not os.path.exists(srt_path):
                raise Exception(f"Subtitle file not found: {srt_path}")
                
            merged_audio_path = os.path.join(self.temp_dir, 'merged_audio.mp3')
            if not os.path.exists(merged_audio_path):
                raise Exception(f"Merged audio file not found: {merged_audio_path}")
            
            # Use forward slashes for FFmpeg on Windows - it handles them better
            escaped_srt_path = srt_path.replace('\\', '/').replace(':', '\\:')
            
            # Get appropriate font for the language
            font_name = self._get_subtitle_font()
            
            if self.background_music_path and self.background_music_path.strip():
                if not os.path.exists(self.background_music_path):
                    self.logger.warning(f"Background music file not found: {self.background_music_path}")
                    # Proceed without background music
                    cmd_final = [
                        'ffmpeg', '-y', '-i', full_video, '-i', merged_audio_path,
                        '-c:v', 'libx264', '-c:a', 'aac',
                        '-vf', f"subtitles='{escaped_srt_path}':force_style='FontSize=16,Bold=1,FontName={font_name},PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=1,Shadow=1,BackColour=&H000000&'",
                        '-shortest', 
                        os.path.join(output_dir, f'{self._get_safe_video_title()}.mp4')
                    ]
                else:
                    cmd_final = [
                        'ffmpeg', '-y', 
                        '-i', full_video, 
                        '-i', merged_audio_path,
                        '-stream_loop', '-1', '-i', os.path.abspath(self.background_music_path),
                        '-c:v', 'libx264', 
                        '-filter_complex', 
                        f"[2:a]volume=0.3[bg];"
                        f"[1:a][bg]amix=inputs=2:duration=first:dropout_transition=2[mixed_audio];"
                        f"[0:v]subtitles='{escaped_srt_path}':force_style='FontSize=26,Bold=1,FontName={font_name},PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=1,Shadow=1,BackColour=&H000000&'[video_with_subs]",
                        '-map', '[video_with_subs]',
                        '-map', '[mixed_audio]',
                        '-c:a', 'aac',
                        '-shortest',
                        os.path.join(output_dir, f'{self._get_safe_video_title()}.mp4')
                    ]
            else:
                cmd_final = [
                    'ffmpeg', '-y', '-i', full_video, '-i', merged_audio_path,
                    '-c:v', 'libx264', '-c:a', 'aac',
                    '-vf', f"subtitles='{escaped_srt_path}':force_style='FontSize=16,Bold=1,FontName={font_name},PrimaryColour=&HFFFFFF&,OutlineColour=&H000000&,Outline=1,Shadow=1,BackColour=&H000000&'",
                    '-shortest', 
                    os.path.join(output_dir, f'{self._get_safe_video_title()}.mp4')
                ]

            gc.collect()
            self._safe_subprocess_run(cmd_final, timeout=2400)  # Increased timeout for final assembly
            self.progress_update.emit(100)  # Update progress to 100% after final assembly
            
            # Verify final output
            final_output = os.path.join(output_dir, f'{self._get_safe_video_title()}.mp4')
            if not os.path.exists(final_output):
                raise Exception("Failed to create final video output")
                
            self.logger.info("Video assembly completed successfully")

    def _get_duration(self, file: str) -> float:
        """Get duration of a media file"""
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', 
            os.path.abspath(file)
        ]
        result = self._safe_subprocess_run(cmd, timeout=30)
        return float(result.stdout.strip())

    def _safe_api_call(self, func, *args, **kwargs):
        """Safe wrapper for API calls with retry logic"""
        for attempt in range(3):
            try:
                self._check_cancelled()
                # Run API call in an executor to avoid blocking
                result = func(*args, **kwargs)
                gc.collect()
                return result
            except Exception as e:
                self.logger.warning(f"API call failed, attempt {attempt + 1}/3: {e}")
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)

    def _merge_srt_files(self, output_dir: str, num_files: int) -> None:
        """Merge multiple SRT files into one, adjusting timestamps (sync)"""
        # Get the correct paths for the new folder structure
        paths = self._get_output_paths(output_dir)
        voice_over_dir = paths['voice_over']
        
        total_offset = 0
        merged_content = []
        subtitle_number = 1
        for i in range(1, num_files + 1):
            srt_path = os.path.join(voice_over_dir, f"subtitle{i}.srt")
            if not os.path.exists(srt_path):
                self.logger.warning(f"Missing SRT file: {srt_path}")
                continue
            with open(srt_path, 'r', encoding='utf-8') as f:
                content = f.read().strip().split('\n\n')
            for entry in content:
                lines = entry.split('\n')
                if len(lines) < 3:
                    continue
                # Parse timestamps
                timestamps = lines[1].split(' --> ')
                start_time = self._parse_srt_time(timestamps[0]) + total_offset
                end_time = self._parse_srt_time(timestamps[1]) + total_offset
                # Format new entry
                new_entry = f"{subtitle_number}\n"
                new_entry += f"{self._format_srt_time(start_time)} --> {self._format_srt_time(end_time)}\n"
                new_entry += '\n'.join(lines[2:])
                merged_content.append(new_entry)
                subtitle_number += 1
            # Get duration of current file to add to offset
            audio_path = os.path.join(voice_over_dir, f"audio{i}.wav")
            if os.path.exists(audio_path):
                total_offset += self._get_duration(audio_path)
        # Write merged SRT
        with open(os.path.join(voice_over_dir, "subtitle.srt"), 'w', encoding='utf-8') as f:
            f.write('\n\n'.join(merged_content))

    def _parse_srt_time(self, time_str: str) -> float:
        """Convert SRT timestamp to seconds"""
        h, m, s = time_str.replace(',', '.').split(':')
        return float(h) * 3600 + float(m) * 60 + float(s)

    def _format_srt_time(self, seconds: float) -> str:
        """Convert seconds to SRT timestamp format"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        msecs = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{msecs:03d}"

    def run(self):
        """Entry point for QThread, runs the async run method"""
        try:
            self.start_time = time.time()
            self.logger.info(f"🚀 Starting video generation at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            
            try:
                # Create temp directory
                self.temp_dir = create_temp_dir()
                self.logger.info(f"Created temporary directory: {self.temp_dir}")
                
                # Initialize
                with self._step_timer("Initialization"):
                    self.operation_update.emit("Initializing")
                    output_dir = create_output_directory(self.video_title, self.channel_name)
                    openai_helper = OpenAIHelper(self.api_key)
                    self.progress_update.emit(5)

                # Generate scripts
                intro_script, looping_script, outro_script = self._generate_scripts(openai_helper)
                total_script = sanitize_for_script(f"{intro_script}\n\n{looping_script}\n\n{outro_script}")
                
                # Save script in main directory with proper filename
                safe_title = self._get_safe_video_title()
                with open(os.path.join(output_dir, f'{safe_title}.txt'), 'w', encoding='utf-8') as file:
                    file.write(total_script)

                # Generate thumbnail
                self._generate_thumbnail(output_dir, openai_helper)

                # Generate images
                self._generate_images(total_script, output_dir, openai_helper)

                # Generate audio and transcriptions
                with self._step_timer("Audio Generation"):
                    self.operation_update.emit("Generating Audio and Transcriptions")
                    audio_chunks = split_text_into_chunks(total_script, -1, self.word_limit)
                    
                    self.logger.info(f"🎵 Generated {len(audio_chunks)} audio chunks (word limit {self.word_limit})")
                    for i, chunk in enumerate(audio_chunks[:3]):  # Log first 3 chunks for debugging
                        self.logger.info(f"   Audio Chunk {i+1}: {chunk[:100]}{'...' if len(chunk) > 100 else ''}")
                    
                    self._generate_audio_parallel(audio_chunks, output_dir, max_workers=2)

                    # Merge audio files
                    self.logger.info("Merging audio files...")
                    audio_list_file = os.path.join(self.temp_dir, 'audios.txt')
                    # Get the correct paths for the new folder structure
                    paths = self._get_output_paths(output_dir)
                    voice_over_dir = paths['voice_over']
                    
                    with open(audio_list_file, 'w', encoding='utf-8') as f:
                        for i in range(1, len(audio_chunks) + 1):
                            # Use forward slashes for better cross-platform compatibility
                            path = os.path.abspath(os.path.join(voice_over_dir, f"audio{i}.wav")).replace('\\', '/')
                            # Escape single quotes in the path for FFmpeg concat format
                            # Replace single quotes with escaped single quotes
                            escaped_path = path.replace("'", "\\'")
                            f.write(f"file '{escaped_path}'\n")

                    merged_wav = os.path.join(self.temp_dir, 'merged_audio.wav')
                    self._safe_subprocess_run([
                        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                        '-i', audio_list_file, 
                        '-c', 'copy',
                        merged_wav
                    ], timeout=450)

                    # Verify merged audio was created
                    if not os.path.exists(merged_wav):
                        raise Exception("Failed to merge audio files")

                    # Convert to MP3
                    self.logger.info("Converting merged audio to MP3...")
                    merged_mp3 = os.path.join(self.temp_dir, 'merged_audio.mp3')
                    self._safe_subprocess_run([
                        'ffmpeg', '-y', '-i', merged_wav,
                        '-c:a', 'libmp3lame',
                        '-b:a', '128k',
                        '-ar', '44100',
                        merged_mp3
                    ], timeout=360)

                    # Verify MP3 conversion
                    if not os.path.exists(merged_mp3):
                        raise Exception("Failed to convert audio to MP3")

                    # Merge SRT files
                    self.logger.info("📝 Merging transcription files...")
                    self._merge_srt_files(output_dir, len(audio_chunks))

                    audio_duration = self._get_duration(merged_mp3)
                    self.logger.info(f"Total audio duration: {audio_duration:.2f} seconds")

                # Assemble video
                self.operation_update.emit("Assembling Final Video")
                self._assemble_video(output_dir, audio_duration, self.image_count)

                self._log_runtime_summary()
                
                try:
                    description = get_first_paragraph(intro_script)
                    self.generation_finished.emit(description)
                except Exception as e:
                    self.logger.warning(f"Error extracting description: {e}")
                    self.generation_finished.emit("Generation completed with errors")

            except Exception as e:
                # Clean up any remaining processes before reporting error
                self._cleanup_processes()
                
                if self.start_time:
                    error_runtime = time.time() - self.start_time
                    self.logger.error(f"❌ Video generation failed after {self._format_duration(error_runtime)}: {e}")
                else:
                    self.logger.error(f"❌ Video generation failed: {e}")
                
                # Log system state for debugging
                try:
                    import psutil
                    memory = psutil.virtual_memory()
                    self.logger.error(f"System memory usage at failure: {memory.percent}%")
                    cpu_percent = psutil.cpu_percent()
                    self.logger.error(f"System CPU usage at failure: {cpu_percent}%")
                except ImportError:
                    pass
                
                self.error_occurred.emit(str(e))
                traceback.print_exc()
            finally:
                # Ensure cleanup happens regardless of success or failure
                self._cleanup_processes()
                if self.temp_dir:
                    self.logger.info(f"Cleaning up temporary directory: {self.temp_dir}")
                    cleanup_temp_dir(self.temp_dir)
                    self.temp_dir = ""
        except Exception as e:
            # Clean up any remaining processes before reporting error
            self._cleanup_processes()
            
            if self.start_time:
                error_runtime = time.time() - self.start_time
                self.logger.error(f"❌ Video generation failed after {self._format_duration(error_runtime)}: {e}")
            else:
                self.logger.error(f"❌ Video generation failed: {e}")
            
            # Log system state for debugging
            try:
                import psutil
                memory = psutil.virtual_memory()
                self.logger.error(f"System memory usage at failure: {memory.percent}%")
                cpu_percent = psutil.cpu_percent()
                self.logger.error(f"System CPU usage at failure: {cpu_percent}%")
            except ImportError:
                pass
            
            self.error_occurred.emit(str(e))
            traceback.print_exc()
        finally:
            # Ensure cleanup happens regardless of success or failure
            self._cleanup_processes()
            if self.temp_dir:
                self.logger.info(f"Cleaning up temporary directory: {self.temp_dir}")
                cleanup_temp_dir(self.temp_dir)
                self.temp_dir = ""