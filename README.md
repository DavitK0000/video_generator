# Video Generator

A powerful Python-based video generation and automation tool that helps create and manage video content with features like subtitle generation, background music integration, and YouTube uploading capabilities.

## Features

- Automated video generation and processing
- Subtitle generation and integration
- Background music handling
- YouTube upload automation
- Bulk video processing support
- Stable configuration management
- Logging and monitoring

## Prerequisites

- Python 3.8 or higher
- Windows 10 or higher
- Google API credentials (for YouTube integration)

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd video_generator
```

2. Create and activate a virtual environment:
```bash
python -m venv .venv
.venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure Google Authentication:
   - Place your `google_auth.json` credentials file in the root directory
   - Update `accounts.json` with your account settings

## Project Structure

- `main.py`: Main application entry point
- `bulk.py`: Bulk video processing functionality
- `worker.py`: Core video generation worker
- `uploader.py`: YouTube upload functionality
- `utils.py`: Utility functions
- `subtitle.py`: Subtitle generation and processing
- `stable.py`: Configuration management
- `variables.py`: Global variables and settings
- `accounts.py`: Account management
- `log.py`: Logging functionality

## Usage

### Basic Video Generation
```python
python main.py
```

### Bulk Processing
```python
python bulk.py
```

### Configuration

The project uses several configuration files:
- `accounts.json`: Account credentials and settings
- `google_auth.json`: Google API authentication
- `bulk_test.xlsx`: Template for bulk video processing

### Directory Structure

- `background_music/`: Background music assets
- `presets/`: Preset configurations
- `logs/`: Application logs
- `reference/`: Reference materials
- `workflows/`: Workflow configurations

## Variables and Prompts

### Thumbnail and Image Prompts
- `$intro`: First part of the script
- `$title`: Title of the video

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request

## License

[Add your license information here]

## Support

For support and questions, please [create an issue](repository-issues-url) or contact the maintainers.
