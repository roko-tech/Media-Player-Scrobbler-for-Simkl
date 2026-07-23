"""
Media Player Classic Qute Theater (MPC-QT) integration for Media Player Scrobbler for SIMKL.
Allows getting position and duration data from MPC-QT through its web interface.
"""

import re
import logging
import requests
import platform
import os
from pathlib import Path

# Only import Windows-specific modules on Windows
PLATFORM = platform.system().lower()
if PLATFORM == 'windows':
    try:
        import winreg
    except ImportError:
        winreg = None

# Configure module logging
logger = logging.getLogger(__name__)

# MPC variables pattern
PATTERN = re.compile(r'\<p id=\"([a-z]+)\"\>(.*?)\<', re.MULTILINE)

class MPCQTIntegration:
    """MPC-QT integration via web interface"""
    
    def __init__(self, base_url=None):
        """
        Initialize MPC-QT integration.
        
        Args:
            base_url: Optional base URL for MPC-QT web interface. If None, auto-detect will be used.
        """
        self.name = 'mpc-qt'
        self.platform = platform.system().lower()
        self.default_ports = [13579]  # MPC-QT uses the same port as MPC-HC
        
        # Set up base URL
        if base_url:
            self.base_url = base_url
        else:
            # Try to auto-detect port from registry
            self.base_url = self._auto_detect_url()
            
        # Session for requests
        self.session = requests.Session()
        self.session.timeout = 1.0  # Short timeout to prevent hanging
        
        # Flag to remember which port worked last
        self.working_port = None
        
        logger.info(f"MPC-QT integration initialized with base URL: {self.base_url}")

    def _auto_detect_url(self):
        """Auto-detect MPC-QT web interface URL from registry"""
        if self.platform != 'windows':
            return "http://localhost:13579"  # Default port for non-Windows
            
        try:
            # Try to get port from registry - MPC-QT may use its own registry key in the future
            port = self._read_registry_port()
            if port:
                logger.info(f"Found MPC-QT port in registry: {port}")
                return f"http://localhost:{port}"
        except Exception as e:
            logger.debug(f"Could not read MPC-QT port from registry: {e}")
            
        # Default to standard port
        logger.debug("Using default MPC-QT port 13579")
        return "http://localhost:13579"
        
    def _read_registry_port(self):
        """Read MPC-QT web interface port from Windows registry"""
        try:
            # MPC-QT might create its own registry key in the future
            # Currently it would use the standard web interface port 13579
            qt_path = "Software\\MPC-QT\\Settings"
            hkey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, qt_path)
            port = winreg.QueryValueEx(hkey, "WebServerPort")[0]
            return port
        except FileNotFoundError:
            # If not found, just use default
            return 13579
        except Exception as e:
            logger.debug(f"Error reading MPC-QT port from registry: {e}")
            return 13579
            
    def _get_variables_url(self, port=None):
        """Get the variables.html URL for the specified port"""
        if port:
            return f"http://localhost:{port}/variables.html"
        elif self.working_port:
            return f"http://localhost:{self.working_port}/variables.html"
        else:
            base = self.base_url.split(':')
            if len(base) >= 3:  # Protocol + host + port
                host = base[1].strip('/')
                port = base[2].split('/')[0]  # Remove path if any
                return f"http://{host}:{port}/variables.html"
            else:
                return f"{self.base_url}/variables.html"
    
    def get_vars(self, port=None):
        """Get variables from MPC-QT web interface"""
        url = self._get_variables_url(port)
        try:
            response = self.session.get(url, timeout=0.5)
            if response.status_code == 200:
                text = response.content.decode("utf-8", errors="ignore")
                matches = PATTERN.findall(text)
                if port:
                    self.working_port = port  # Remember working port
                return dict(matches)
            else:
                # Raise an exception if the web interface is unreachable (non-200 status)
                raise requests.RequestException(f"MPC-QT web interface returned status {response.status_code} for {url}")
        except requests.RequestException as e:
            # Always raise so the notification logic in media_scrobbler.py is triggered
            raise
    
    def _iter_candidate_vars(self):
        """Yield responses from every candidate, raising only if all connections fail."""
        ports = []
        for port in [self.working_port, *self.default_ports]:
            if port is not None and port not in ports:
                ports.append(port)

        last_error = None
        received_response = False
        for port in ports:
            try:
                variables = self.get_vars(port)
                received_response = True
                yield port, variables
            except requests.RequestException as exc:
                last_error = exc
                logger.debug("MPC-QT candidate port %s failed: %s", port, exc)

        if not received_response and last_error:
            raise last_error

    def get_position_duration(self, process_name=None):
        """Get position and duration, trying every configured MPC-QT port."""
        for port, variables in self._iter_candidate_vars():
            if variables and variables.get('duration', '0') != '0':
                position = int(variables.get('position', 0)) / 1000.0
                duration = int(variables.get('duration', 0)) / 1000.0
                if port != self.working_port:
                    logger.info("Successfully connected to MPC-QT web interface on port %s", port)
                    self.working_port = port
                return position, duration
        return None, None  # Failed to get position/duration
        
    def is_paused(self):
        """Return the pause state from the first responsive MPC-QT candidate."""
        for _, variables in self._iter_candidate_vars():
            if variables:
                return variables.get('state', '') == '2'
        return None  # Couldn't determine pause state
    
    def get_current_filepath(self, process_name=None):
        """Get the current filepath, trying every configured MPC-QT port."""
        for port, variables in self._iter_candidate_vars():
            filepath = variables.get('filepath', '') if variables else ''
            if filepath:
                logger.debug("Retrieved filepath from MPC-QT on port %s: %s", port, filepath)
                self.working_port = port
                return filepath
        return None