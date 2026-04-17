"""
Entry point for PyInstaller frozen executable.
This wrapper properly sets up the Python path before importing the daemon package.
"""
import sys
import os

# When frozen by PyInstaller, sys._MEIPASS contains the bundled files directory
if getattr(sys, 'frozen', False):
    # Get the directory where the executable is located
    app_dir = os.path.dirname(sys.executable)
    
    # Add app directory to Python path so 'daemon' package can be imported
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    
    # Load .env file from app directory if it exists
    env_file = os.path.join(app_dir, '.env')
    if os.path.isfile(env_file):
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Only set if not already set (env vars take precedence)
                        if key not in os.environ:
                            os.environ[key] = value

# Now import and run the main function
import daemon.__main__
daemon.__main__.main()
