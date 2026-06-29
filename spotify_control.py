import logging
import re
import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth

logger = logging.getLogger(__name__)

class SpotifyController:
    """
    Controls Spotify playback using the official spotipy library.
    Handles device transfer automatically to ensure playback works.
    """

    def __init__(self, config: dict):
        """
        Args:
            config: The spotify configuration section from config.yaml
        """
        self.config = config
        self.client_id = os.environ.get("SPOTIFY_CLIENT_ID") or config.get("client_id", "")
        self.client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET") or config.get("client_secret", "")
        self.redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI") or config.get("redirect_uri", "http://127.0.0.1:25566/callback")
        self.enabled = config.get("enabled", False)
        self._sp = None
        self._auth_manager = None

    def is_configured(self) -> bool:
        """Return True if Spotify credentials are set and enabled."""
        return bool(self.enabled and self.client_id and self.client_secret)

    def load(self) -> bool:
        """Initialize the Spotify client metadata. Does not block or perform network calls on startup."""
        if not self.is_configured():
            return False
        
        try:
            scope = "user-modify-playback-state user-read-playback-state"
            self._auth_manager = SpotifyOAuth(
                client_id=self.client_id,
                client_secret=self.client_secret,
                redirect_uri=self.redirect_uri,
                scope=scope,
                open_browser=False  # Only open when requested by a voice command
            )
            self._sp = spotipy.Spotify(auth_manager=self._auth_manager)
            logger.info("Spotify controller metadata loaded.")
            return True
        except Exception as e:
            logger.error("Failed to initialize Spotify auth manager: %s", e)
            self._sp = None
            self._auth_manager = None
            return False

    def _ensure_auth(self) -> bool:
        """Ensure token is valid and authenticated. Triggers browser authentication only when needed."""
        if not self._sp or not self._auth_manager:
            return False
        try:
            # Check if token exists in cache and is valid
            token = self._auth_manager.get_cached_token()
            if not token:
                logger.warning("Spotify token not found in cache. Running Spotipy auth flow...")
                # Temporarily enable browser opening for the flow
                self._auth_manager.open_browser = True
                # This will start the local server on redirect_uri port and wait for redirect code
                token = self._auth_manager.get_access_token(as_dict=False)
                if token:
                    logger.info("Successfully authenticated with Spotify.")
                    return True
                return False
            return True
        except Exception as e:
            logger.error("Spotify auth check failed: %s", e)
            return False

    def _get_active_device(self):
        """Helper to get an active device, or transfer to the first available one."""
        if not self._sp:
            return None
        
        try:
            devices = self._sp.devices()
            device_list = devices.get("devices", [])
            
            if not device_list:
                return None
            
            # Find active device
            for dev in device_list:
                if dev.get("is_active"):
                    return dev.get("id")
            
            # If no active device but devices exist, transfer to the first one
            first_dev_id = device_list[0].get("id")
            logger.info("No active Spotify device found. Transferring playback to: %s", device_list[0].get("name"))
            self._sp.transfer_playback(first_dev_id, force_play=False)
            return first_dev_id
        except Exception as e:
            logger.error("Error getting Spotify devices: %s", e)
            return None

    def play(self) -> str:
        """Resume playback on Spotify."""
        if not self._sp:
            return "Spotify is not configured. Please check your config yaml keys."
        if not self._ensure_auth():
            return "I opened a Spotify authorization window in your web browser. Please log in and click Agree, then try again."
        try:
            device_id = self._get_active_device()
            self._sp.start_playback(device_id=device_id)
            return "Playing music on Spotify."
        except Exception as e:
            logger.error("Spotify play failed: %s", e)
            if "NO_ACTIVE_DEVICE" in str(e) or "Restriction" in str(e):
                return "I couldn't find an active Spotify device. Please open the Spotify app on your PC or phone first."
            return "I had trouble starting Spotify playback."

    def pause(self) -> str:
        """Pause playback on Spotify."""
        if not self._sp:
            return "Spotify is not configured."
        if not self._ensure_auth():
            return "Please authorize Spotify in your web browser first."
        try:
            device_id = self._get_active_device()
            self._sp.pause_playback(device_id=device_id)
            return "Pausing Spotify music."
        except Exception as e:
            logger.error("Spotify pause failed: %s", e)
            if "NO_ACTIVE_DEVICE" in str(e):
                return "No active Spotify device found to pause."
            return "I couldn't pause Spotify playback."

    def next(self) -> str:
        """Skip to the next track on Spotify."""
        if not self._sp:
            return "Spotify is not configured."
        if not self._ensure_auth():
            return "Please authorize Spotify in your web browser first."
        try:
            device_id = self._get_active_device()
            self._sp.next_track(device_id=device_id)
            return "Skipping to the next song."
        except Exception as e:
            logger.error("Spotify next failed: %s", e)
            return "I had trouble skipping the song."

    def previous(self) -> str:
        """Go back to the previous track on Spotify."""
        if not self._sp:
            return "Spotify is not configured."
        if not self._ensure_auth():
            return "Please authorize Spotify in your web browser first."
        try:
            device_id = self._get_active_device()
            self._sp.previous_track(device_id=device_id)
            return "Playing the previous song."
        except Exception as e:
            logger.error("Spotify previous failed: %s", e)
            return "I had trouble playing the previous song."

    def search_and_play(self, query: str) -> str:
        """Search Spotify for a track or playlist and play it with smart matching."""
        if not self._sp:
            return "Spotify is not configured."
        if not self._ensure_auth():
            return "I opened a Spotify authorization window in your web browser. Please log in and click Agree, then try again."
        
        is_playlist = "playlist" in query.lower()
        
        # Clean up queries
        search_query = query.lower()
        search_query = re.sub(r"\bon spotify\b", "", search_query).strip()
        search_query = re.sub(r"\bplay\s+", "", search_query).strip()
        
        if is_playlist:
            search_query = re.sub(r"\b(my\s+)?playlist\b", "", search_query).strip()
            
        if not search_query:
            return "What would you like me to play on Spotify?"

        try:
            device_id = self._get_active_device()
            
            if is_playlist:
                # Global playlist search (does not require special private scopes)
                results = self._sp.search(q=search_query, limit=3, type="playlist")
                playlists = results.get("playlists", {}).get("items", [])
                if not playlists:
                    return f"I couldn't find any playlists matching '{search_query}' on Spotify."
                
                playlist = playlists[0]
                playlist_uri = playlist.get("uri")
                playlist_name = playlist.get("name")
                
                self._sp.start_playback(device_id=device_id, context_uri=playlist_uri)
                return f"Playing playlist '{playlist_name}' on Spotify."
                
            else:
                # Track search with strict validation
                song_part = search_query
                artist_part = ""
                if " by " in search_query:
                    parts = search_query.split(" by ")
                    song_part = parts[0].strip()
                    artist_part = parts[1].strip()
                elif " and " in search_query:
                    parts = search_query.split(" and ")
                    song_part = parts[0].strip()
                    artist_part = parts[1].strip()

                results = self._sp.search(q=search_query, limit=5, type="track")
                tracks = results.get("tracks", {}).get("items", [])
                
                if not tracks:
                    return f"I couldn't find any songs matching '{search_query}' on Spotify."
                
                best_track = None
                best_score = 0.0
                
                for track in tracks:
                    track_name = track.get("name", "").lower()
                    artists = [a.get("name", "").lower() for a in track.get("artists", [])]
                    
                    # Score the track match
                    title_match = (song_part in track_name) or (track_name in song_part)
                    
                    artist_match = True
                    if artist_part:
                        artist_match = False
                        for art in artists:
                            if (artist_part in art) or (art in artist_part):
                                artist_match = True
                                break
                            # Alias mapping: m&m / m and m / m&m's -> eminem
                            if artist_part in ("m&m", "m and m", "mnm", "m&ms") and "eminem" in art:
                                artist_match = True
                                break
                    
                    score = 0.0
                    if title_match:
                        score += 0.5
                    if artist_match:
                        score += 0.5
                        
                    if score > best_score:
                        best_score = score
                        best_track = track
                
                # If we found a validated match, use it. Otherwise fallback to the top result
                if best_track and best_score >= 0.5:
                    track = best_track
                else:
                    track = tracks[0]
                
                track_uri = track.get("uri")
                track_name = track.get("name")
                artist_name = track.get("artists", [{}])[0].get("name", "Unknown Artist")
                
                self._sp.start_playback(device_id=device_id, uris=[track_uri])
                return f"Playing '{track_name}' by {artist_name} on Spotify."
                
        except Exception as e:
            logger.error("Spotify search_and_play failed: %s", e)
            if "NO_ACTIVE_DEVICE" in str(e):
                return "Please open the Spotify app on your PC or phone first so I can play that."
            return f"I had trouble playing '{search_query}' on Spotify."
