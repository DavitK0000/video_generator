import json
import os
import logging
import base64
import io
from typing import Literal, Dict, Any, Optional
from openai import OpenAI
from PIL import Image
import re
import unicodedata
import sys

class OpenAIHelper:
    """Helper class for interacting with OpenAI APIs"""

    def __init__(
        self,
        api_key: str
    ):
        """
        Initialize OpenAI helper
        Args:
            api_key: OpenAI API key
        """
        self.openai_client = OpenAI(api_key=api_key)
        self.logger = logging.getLogger(__name__)
        self.logger.info("OpenAI helper initialized")

    def generate_text(
        self,
        prompt: str,
        model="gpt-4o-mini",
        max_tokens=16000,
        temperature=1.0,
        top_p=1.0,
        prev_id: str = None,
    ):
        response = self.openai_client.responses.create(
            model=model,
            input=prompt,
            max_output_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            previous_response_id=prev_id
        )
        if response.error is not None:
            return (None, response.error)
        return (response.output_text, response.id)

    def generate_image(
        self,
        prompt: str,
        model="gpt-image-1",
        size: Literal['square', 'landscape', 'portrait'] = 'square',
        quality: Literal['high', 'medium', 'low', 'hd', 'standard'] = 'high'
    ):
        sizeData = {
            "square": "1024x1024",
            "landscape": "1536x1024",
            "portrait": "1024x1536"
        }
        response = self.openai_client.images.generate(
            model=model,
            prompt=prompt,
            size=sizeData[size],
            quality=quality,
            moderation='low'
        )
        result_b64 = response.data[0].b64_json
        image_data = base64.b64decode(result_b64)

        return image_data

    def generate_audio(
        self,
        prompt: str,
        model="gpt-4o-mini-tts",
        voice="onyx"
    ):
        result = self.openai_client.audio.speech.create(
            model="gpt-4o-mini-tts",
            voice=voice,
            input=prompt
        )
        return result.content


def save_image_base64(
    image_data: bytes,
    output_file: str,
    width=1280,
    height=720,
):
    img = Image.open(io.BytesIO(image_data))
    resized_img = img.resize((width, height), Image.Resampling.LANCZOS)
    with open(output_file, 'wb') as f:
        resized_img.save(f, format="JPEG")


def save_audio_as_file(
    audio_data,
    output_file,
):
    with open(output_file, 'wb') as f:
        f.write(audio_data)
    pass


def create_output_directory(video_title: str, channel_name: str = "default") -> str:
    """
    Create output directory with structured hierarchy: ./output/{channel name}/{video title}
    Args:
        video_title: Video title (will be converted to safe folder name)
        channel_name: Channel name for organizing videos
    Returns:
        Path to created directory
    """
    from datetime import datetime
    try:
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
        video_dir = os.path.join(channel_dir, video_title)
        
        # Create all directories in the path
        os.makedirs(video_dir, exist_ok=True)
        
        logging.info(f"Created output directory: {video_dir}")
        return video_dir
    except Exception as e:
        logging.error(f"Failed to create output directory: {str(e)}")
        # Fall back to simple structure
        fallback_dir = os.path.join(base_dir, "output", video_title)
        os.makedirs(fallback_dir, exist_ok=True)
        return fallback_dir


def save_config(config: Dict[str, Any], directory: str) -> bool:
    """
    Save configuration to a JSON file
    Args:
        config: Configuration dictionary
        directory: Directory to save the file
    Returns:
        True if successful, False otherwise
    """
    try:
        # Remove API key from the config before saving
        safe_config = config.copy()
        if "api_key" in safe_config:
            safe_config["api_key"] = "[REDACTED]"
        filepath = os.path.join(directory, "config.json")
        with open(filepath, "w") as f:
            json.dump(safe_config, f, indent=2)
        logging.info(f"Saved configuration to {filepath}")
        return True
    except Exception as e:
        logging.error(f"Failed to save configuration: {str(e)}")
        return False


def load_config(filepath: str) -> Optional[Dict[str, Any]]:
    """
    Load configuration from a JSON file
    Args:
        filepath: Path to the configuration file
    Returns:
        Configuration dictionary if successful, None otherwise
    """
    try:
        with open(filepath, "r") as f:
            config = json.load(f)
        logging.info(f"Loaded configuration from {filepath}")
        return config
    except Exception as e:
        logging.error(f"Failed to load configuration: {str(e)}")
        return None


def get_default_settings() -> Dict[str, Any]:
    """
    Get default settings for the application
    Returns:
        Dictionary with default settings
    """
    return {
        "api_key": "",
        "background_music": "",
        "video_title": "",
        "thumbnail_prompt": "",
        "images_prompt": "",
        "disclaimer": "",
        "intro_prompt": "",
        "looping_prompt": "",
        "outro_prompt": "",
        "prompt_variables": {},
        "loop_length": 3,
        "audio_word_limit": 400,
        "image_count": 3,
        "image_word_limit": 15,
        "thumbnail_model": "runware:100@1",
        "thumbnail_loras": [],
        "image_model": "runware:100@1",
        "image_loras": [],
        "language": "a",  # Default to American English
        "voice": "am_michael"  # Default to Michael voice
    }


def get_settings_filepath() -> str:
    """
    Get the filepath for the settings file
    Returns:
        Path to settings file
    """
    # Create settings directory if it doesn't exist
    os.makedirs("settings", exist_ok=True)
    return os.path.join("settings", "video_generator_settings.json")


def sanitize_for_script(text) -> str:
    return (text
            .replace('\u2018', "'")        # curly single quotes
            .replace('\u2019', "'")        # curly single quotes
            .replace('\u201C', '"')        # curly double quotes
            .replace('\u201D', '"')        # curly double quotes
            .replace('\u2013', '-')        # en dash
            .replace('\u2014', '-')        # em dash
            .replace('\u2026', '...')      # ellipsis
            .replace('\u00a0', ' ')        # non-breaking spaces
            # .replace('\\', '\\\\')         # escape backslashes
            # .replace('"', '\\"')           # escape double quotes
            # .replace('\r\n', '\n')        # escape windows newlines
            # .replace('\n', '\\n')          # escape unix newlines
            .replace('\t', ' ')            # remove tabs
            .strip()                       # trim
            )


def split_text_into_chunks(
    text: str,
    chunks_count,
    word_limit=10,
) -> list:
    """
    Split text into chunks based on sentences, respecting word limit per chunk.

    Args:
        text: Input text to be split
        word_limit: Maximum number of words per chunk
        chunks_count: Maximum number of chunks to return

    Returns:
        List of text chunks
    """

    # Clean the text (similar to JavaScript version)
    raw = text
    cleaned = raw.replace("\\n", "\n")  # Convert literal \n into real newlines
    cleaned = re.sub(r"\s+", " ", cleaned)  # Collapse multiple spaces/newlines
    cleaned = cleaned.strip()

    # Check if text contains CJK characters
    has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff]', cleaned))
    
    if has_cjk:
        # For CJK languages, use character-based chunking
        # Split into sentences using CJK punctuation
        sentences = re.findall(r'[^。！？…；，]+[。！？…；，]*', cleaned)
        
        # Remove empty sentences and clean up
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if not sentences:
            sentences = [cleaned]
        
        chunks = []
        current_chunk = ""
        char_limit = word_limit * 3  # For CJK, use more characters per chunk
        
        for sentence in sentences:
            sentence = sentence.strip()
            
            # If adding this sentence would exceed the limit, start a new chunk
            if len(current_chunk) + len(sentence) > char_limit and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += sentence
                else:
                    current_chunk = sentence
        
        # Add the last chunk if there's content left
        if current_chunk:
            chunks.append(current_chunk.strip())
            
    else:
        # For non-CJK text, use word-based splitting
        # Split into sentences - support both Western punctuation
        sentences = re.findall(r'[^\.!\?]+[\.!\?]+(?:\s|$)', cleaned) or []
        
        # Fallback: if no sentences found, split by newlines or use entire text
        if not sentences:
            sentences = [line.strip() for line in cleaned.split('\n') if line.strip()]
            if not sentences:
                sentences = [cleaned]

        chunks = []
        current_words = []

        for sentence in sentences:
            sentence = sentence.strip()
            sentence_words = sentence.split()
            
            if len(current_words) + len(sentence_words) <= word_limit:
                current_words.extend(sentence_words)
            else:
                if len(current_words) > 0:
                    chunks.append(" ".join(current_words))
                current_words = sentence_words

        # Add the last chunk if there are any words left
        if current_words:
            chunks.append(" ".join(current_words))

    # Limit the number of chunks returned
    if chunks_count == -1:
        return chunks

    return chunks[:chunks_count]

def split_text_into_chunks_image(
    text: str,
    chunks_count,
    word_limit=10,
) -> list:
    """
    Split text into chunks based on sentences, respecting word limit per chunk.

    Args:
        text: Input text to be split
        word_limit: Maximum number of words per chunk
        chunks_count: Maximum number of chunks to return

    Returns:
        List of text chunks
    """
    
    # Clean the text (similar to JavaScript version)
    raw = text
    cleaned = raw.replace("\\n", "\n")  # Convert literal \n into real newlines
    cleaned = re.sub(r"\s+", " ", cleaned)  # Collapse multiple spaces/newlines
    cleaned = cleaned.strip()

    # Check if text contains CJK characters
    has_cjk = bool(re.search(r'[\u4e00-\u9fff\u3400-\u4dbf\u3040-\u309f\u30a0-\u30ff]', cleaned))
    
    if has_cjk:
        # For CJK languages, use character-based chunking
        # Split into sentences using CJK punctuation
        sentences = re.findall(r'[^。！？…；，]+[。！？…；，]*', cleaned)
        
        # Remove empty sentences and clean up
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if not sentences:
            sentences = [cleaned]
        
        chunks = []
        current_chunk = ""
        char_limit = word_limit * 3  # For CJK, use more characters per chunk
        
        for sentence in sentences:
            sentence = sentence.strip()
            
            # If adding this sentence would exceed the limit, start a new chunk
            if len(current_chunk) + len(sentence) > char_limit and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                if current_chunk:
                    current_chunk += sentence
                else:
                    current_chunk = sentence
        
        # Add the last chunk if there's content left
        if current_chunk:
            chunks.append(current_chunk.strip())
            
    else:
        # For non-CJK text, use word-based splitting
        # Split into sentences - support both Western punctuation
        sentences = re.findall(r'[^\.!\?]+[\.!\?]+(?:\s|$)', cleaned) or []
        
        # Fallback: if no sentences found, split by newlines or use entire text
        if not sentences:
            sentences = [line.strip() for line in cleaned.split('\n') if line.strip()]
            if not sentences:
                sentences = [cleaned]
        
        chunks = []
        current_words = []

        for sentence in sentences:
            sentence = sentence.strip()
            sentence_words = sentence.split()
            
            if len(current_words) + len(sentence_words) <= word_limit:
                current_words.extend(sentence_words)
            else:
                if len(current_words) > 0:
                    chunks.append(" ".join(current_words))
                current_words = sentence_words

        # Add the last chunk if there are any words left
        if current_words:
            chunks.append(" ".join(current_words))

    # Limit the number of chunks returned
    if chunks_count == -1:
        return chunks

    return chunks[:chunks_count]

def get_first_paragraph(text):
    """Extract the first paragraph from a multiline text.

    Args:
        text (str): The multiline text to process.

    Returns:
        str: The first paragraph from the text.
    """
    # Split the text by double newlines (paragraph separators)
    paragraphs = text.split('\n\n')

    # Return the first non-empty paragraph
    for paragraph in paragraphs:
        if paragraph.strip():
            return paragraph.strip()

    # Return empty string if no paragraphs found
    return ""

def title_to_safe_folder_name(title: str) -> str:
    # Start with the original title
    safe_title = title.strip()
    
    # Replace only the characters that are truly problematic for folder names
    # These are the characters that are invalid in Windows, Linux, and macOS file systems
    problematic_chars = {
        '<': '＜',   # Replace with full-width equivalents
        '>': '＞',
        ':': '：',
        '"': '＂',
        '|': '｜',
        '?': '？',
        '*': '＊',
        '/': '／',
        '\\': '＼',
        '\0': '',    # Remove null characters
        '\n': ' ',   # Replace newlines with spaces
        '\r': ' ',   # Replace carriage returns with spaces
        '\t': ' ',   # Replace tabs with spaces
    }
    
    # Replace problematic characters
    for bad_char, replacement in problematic_chars.items():
        safe_title = safe_title.replace(bad_char, replacement)
    
    # Replace smart quotes and em/en dashes with similar Unicode equivalents
    safe_title = safe_title.replace("'", "'").replace("'", "'") \
                          .replace(""", "＂").replace(""", "＂") \
                          .replace("—", "－").replace("–", "－")
    
    # Clean up multiple consecutive spaces
    safe_title = re.sub(r'\s+', ' ', safe_title).strip()
    
    # Handle edge case: if title becomes empty, use a fallback
    if not safe_title:
        import hashlib
        import time
        hash_short = hashlib.md5(title.encode('utf-8')).hexdigest()[:8]
        timestamp = str(int(time.time()))[-6:]
        safe_title = f"video_{timestamp}_{hash_short}"
    
    # Truncate if too long (Windows path limit consideration)
    if len(safe_title) > 100:  # Leave room for file extensions and path
        safe_title = safe_title[:100].rstrip()
    
    return safe_title

def safe_title(title: str) -> str:
    """
    Sanitize title for use in file names.
    
    Args:
        title: The title to sanitize.
        
    Returns:
        A sanitized version of the title.
    """
    # Normalize unicode characters (e.g. é → e, — → -)
    # title = unicodedata.normalize("NFKD", title)
    
    # Replace smart quotes and em/en dashes with ASCII equivalents
    # Replace curly/smart quotes with their Unicode equivalents
    # U+2018 (LEFT SINGLE QUOTATION MARK) → '
    # U+2019 (RIGHT SINGLE QUOTATION MARK) → '
    # U+201C (LEFT DOUBLE QUOTATION MARK) → "
    # U+201D (RIGHT DOUBLE QUOTATION MARK) → "
    # U+2014 (EM DASH) → -
    # U+2013 (EN DASH) → -
    title = title.replace("'", "'").replace("'", "'") \
                 .replace("\"", "\"").replace("\"", "\"") \
                 .replace("—", "-").replace("–", "-")
    # Remove invalid characters for file names
    # return re.sub(r'[<>:"/\\|?*]', '', title).strip()
    return title.strip()[:80]


def format_time(seconds):
    """Convert seconds to SRT time format"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millisecs = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millisecs:03d}"

def group_words(words, min_words=4, max_words=6):
    """Group words into chunks of 4-6 words with their timing info"""
    groups = []
    
    i = 0
    while i < len(words):
        remaining_words = len(words) - i
        
        if remaining_words <= max_words:
            # Take all remaining words
            group = words[i:]
            groups.append(group)
            break
        elif remaining_words < min_words + max_words:
            # Split remaining words more evenly
            group_size = remaining_words // 2 + (remaining_words % 2)
            group = words[i:i+group_size]
            groups.append(group)
            i += group_size
        else:
            # Take max_words
            group = words[i:i+max_words]
            groups.append(group)
            i += max_words
    
    return groups

def write_srt(segments, output_path):
    """Convert segments to SRT format with 4-6 words per line using actual word timestamps"""
    with open(output_path, 'w', encoding='utf-8') as f:
        subtitle_number = 1
        
        for segment in segments:
            # Check if segment has words with timestamps
            if not hasattr(segment, 'words') or not segment.words:
                # Fallback: use segment text and timing
                f.write(f"{subtitle_number}\n")
                f.write(f"{format_time(segment.start)} --> {format_time(segment.end)}\n")
                f.write(f"{segment.text.strip()}\n\n")
                subtitle_number += 1
                continue
            
            # Group words into 4-6 word chunks
            word_groups = group_words(segment.words)
            
            # Create subtitle entries for each word group
            for group in word_groups:
                if not group:
                    continue
                
                # Get timing from first and last word in group
                start_time = format_time(group[0].start)
                end_time = format_time(group[-1].end)
                
                # Combine word texts
                text = ''.join(word.word for word in group).strip()
                
                f.write(f"{subtitle_number}\n")
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{text}\n\n")
                
                subtitle_number += 1
                

def validate_preset_content(file_path):
    """Preset content validation"""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
        
        required_fields = [
            'api_key',
            'thumbnail_prompt',
            'images_prompt',
            'disclaimer',
            'intro_prompt',
            'looping_prompt',
            'outro_prompt',
            'loop_length',
            'audio_word_limit',
            'image_count',
            'image_word_limit',
            'thumbnail_model',
            'thumbnail_loras',
            'image_model',
            'image_loras'
        ]
        
        for field in required_fields:
            if field not in data:
                return False
        
        return True
    except:
        return False

def validate_workflow_content(file_path):
    """Dummy workflow content validation - replace with actual logic"""
    try:
        with open(file_path, 'r') as f:
            data = json.load(f)
            
        prompt_exist = False
        width_exist = False
        height_exist = False
        ksampler_exist = False
        
        for node_num, node in data.items():
            if '_meta' not in node or 'title' not in node['_meta']:
                continue

            if node['_meta']['title'] == 'prompt':
                prompt_exist = True
            
            if node['_meta']['title'] == 'width':
                width_exist = True

            if node['_meta']['title'] == 'height':
                height_exist = True

            if node['_meta']['title'] == 'KSampler':
                ksampler_exist = True
        
        return prompt_exist or width_exist or height_exist or ksampler_exist
    
    except:
        return False
