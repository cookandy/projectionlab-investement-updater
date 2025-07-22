#!/usr/bin/env python3
"""Script to update ProjectionLab accounts with crypto and stock values."""

import json
import logging
import os
import re
import sys
import time
from typing import Dict, List, Optional, Set, Union

import pyotp
import requests
import yaml
from bs4 import BeautifulSoup
from DrissionPage import Chromium, ChromiumOptions
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Constants
LOCK_FILE_PATH = "/tmp/projectionlab_update.lock"
CACHE_DIR = "/tmp/yfinance-cache"
CRYPTO_CACHE_FILE = "/tmp/crypto_prices_cache.json"
EXCHANGE_RATES_CACHE_FILE = "/tmp/exchange_rates_cache.json"
ACCOUNTS_PATH = "/app/accounts.yaml"
VALIDATE_ONLY = os.getenv('VALIDATE_ONLY', 'false').lower() == 'true'
DEFAULT_CRYPTO_IDS = ['bitcoin', 'ethereum']
DEFAULT_CURRENCIES = ["CAD", "EUR", "GBP"]

# Set up logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Set custom cache location for yfinance before importing it
os.environ["YFINANCE_CACHE_DIR"] = CACHE_DIR

# Create the directory with proper permissions
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.chmod(CACHE_DIR, 0o777)
except Exception as e:
    logging.warning(f"Error setting up yfinance cache directory: {e}")

# Now import yfinance
import yfinance as yf

# Set the cache location programmatically
try:
    yf.set_tz_cache_location(CACHE_DIR)
except Exception as e:
    logging.warning(f"Error setting yfinance cache location: {e}")


def get_config_from_env() -> Optional[Dict]:
    """Load configuration from environment variables."""
    # Get raw MFA key from environment
    raw_mfa_key = os.getenv('PL_MFA_KEY')

    # Clean MFA key if it exists
    clean_mfa_key = None
    if raw_mfa_key:
        # Remove spaces, dashes, quotes, and convert to uppercase
        clean_mfa_key = raw_mfa_key.replace(' ', '').replace('-', '').replace('"', '').replace("'", '').upper()

    config = {
        'projectionlab': {
            'username': os.getenv('PL_USERNAME'),
            'password': os.getenv('PL_PASSWORD'),
            'api_key': os.getenv('PL_API_KEY'),
            'mfa_key': clean_mfa_key,  # Store the actual key
            'url': os.getenv('PL_URL', 'https://app.projectionlab.com/login'),
            'time_delay': int(os.getenv('PL_TIME_DELAY', '10'))
        }
    }

    # Create a separate copy for logging with sensitive data redacted
    safe_config = {
        'projectionlab': {
            'username': config['projectionlab']['username'],
            'password': '********' if config['projectionlab']['password'] else None,
            'api_key': '********' if config['projectionlab']['api_key'] else None,
            'mfa_key': '********' if config['projectionlab']['mfa_key'] else None,
            'url': config['projectionlab']['url'],
            'time_delay': config['projectionlab']['time_delay']
        }
    }

    logging.info(f"Configuration loaded from environment variables: {safe_config}")

    # Validate required configuration
    if not config['projectionlab']['username'] or not config['projectionlab']['password'] or not config['projectionlab']['api_key']:
        logging.error("Required ProjectionLab configuration missing. Please set PL_USERNAME, PL_PASSWORD, and PL_API_KEY environment variables.")
        return None

    return config


def obtain_lock() -> bool:
    """Try to obtain a lock file to prevent concurrent execution."""
    # Check if lock file exists
    if os.path.exists(LOCK_FILE_PATH):
        # Check if the lock file is stale (older than 1 hour)
        lock_file_time = os.path.getmtime(LOCK_FILE_PATH)
        current_time = time.time()

        if current_time - lock_file_time > 3600:  # 1 hour in seconds
            logging.warning(f"Found stale lock file (> 1 hour old). Removing and continuing.")
            try:
                os.remove(LOCK_FILE_PATH)
            except Exception as e:
                logging.error(f"Error removing stale lock file: {e}")
                return False
        else:
            # Lock file exists and is not stale
            logging.warning("Another instance is already running. Exiting.")
            return False

    # Create the lock file
    try:
        with open(LOCK_FILE_PATH, 'w') as f:
            f.write(str(os.getpid()))
        logging.info(f"Lock file created: {LOCK_FILE_PATH}")
        return True
    except Exception as e:
        logging.error(f"Error creating lock file: {e}")
        return False


def release_lock() -> None:
    """Release the lock file."""
    try:
        if os.path.exists(LOCK_FILE_PATH):
            os.remove(LOCK_FILE_PATH)
            logging.info(f"Lock file removed: {LOCK_FILE_PATH}")
    except Exception as e:
        logging.error(f"Error removing lock file: {e}")


def load_yaml(file_path: str) -> Dict:
    """Load data from a YAML file."""
    try:
        with open(file_path, 'r') as f:
            data = yaml.safe_load(f)
        logging.info(f"Data loaded from {file_path}")
        return data
    except Exception as e:
        logging.error(f"Error loading data from {file_path}: {e}")
        return {}

def get_totp_from_secret(secret, email):
    """Generate TOTP code from secret key"""
    try:
        totp = pyotp.parse_uri(f'otpauth://totp/ProjectionLab:{email}?secret={secret}&issuer=ProjectionLab&algorithm=SHA1&digits=6')
        code = totp.now()
        logging.info(f"Generated TOTP code: {code}")
        return code
    except Exception as e:
        logging.error(f"Error generating TOTP code: {e}")
        return None


def get_crypto_prices(crypto_ids: Optional[List[str]] = None, max_retries: int = 3, retry_delay: int = 5) -> Dict[str, float]:
    """Get current prices for specified cryptocurrencies in USD with retry logic."""
    if crypto_ids is None:
        crypto_ids = DEFAULT_CRYPTO_IDS  # Default coins if none specified

    # Convert list to comma-separated string for API
    ids_param = ','.join(crypto_ids)

    for attempt in range(max_retries):
        try:
            logging.info(f"Requesting cryptocurrency prices from CoinGecko API (attempt {attempt+1}/{max_retries})...")
            response = requests.get(
                f'https://api.coingecko.com/api/v3/simple/price?ids={ids_param}&vs_currencies=usd',
                timeout=10  # Add timeout to prevent hanging requests
            )

            # Check for rate limiting (HTTP 429)
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', retry_delay))
                logging.warning(f"Rate limit hit (429). Waiting {retry_after} seconds before retry...")
                time.sleep(retry_after)
                continue

            # Check for other errors
            response.raise_for_status()

            data = response.json()
            prices = {}

            # Extract prices for all requested cryptocurrencies
            for crypto_id in crypto_ids:
                if crypto_id in data and 'usd' in data[crypto_id]:
                    price = data[crypto_id]['usd']
                    prices[crypto_id] = price
                    logging.info(f"Current {crypto_id} price: ${price:,.2f}")
                else:
                    logging.warning(f"Price for {crypto_id} not found in API response")
                    prices[crypto_id] = None

            return prices
        except requests.exceptions.RequestException as e:
            logging.error(f"Request error (attempt {attempt+1}/{max_retries}): {e}")
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            logging.error(f"Data parsing error (attempt {attempt+1}/{max_retries}): {e}")
        except Exception as e:
            logging.error(f"Unexpected error (attempt {attempt+1}/{max_retries}): {e}")

        # Wait before retry (with exponential backoff)
        if attempt < max_retries - 1:
            backoff_time = retry_delay * (2 ** attempt)
            logging.info(f"Waiting {backoff_time} seconds before retry...")
            time.sleep(backoff_time)
        else:
            logging.error("Maximum retry attempts reached")

    return {}


def get_crypto_ids_from_accounts(accounts: List[Dict]) -> List[str]:
    """Extract all unique cryptocurrency IDs from accounts configuration."""
    crypto_ids = set()

    for account in accounts:
        if 'assets' in account and 'crypto' in account['assets']:
            crypto_assets = account['assets']['crypto']
            # Add all crypto asset keys to the set
            for crypto_id in crypto_assets.keys():
                crypto_ids.add(crypto_id)

    # If no crypto assets found, use defaults
    if not crypto_ids:
        crypto_ids = set(DEFAULT_CRYPTO_IDS)

    logging.info(f"Found cryptocurrencies in accounts: {', '.join(crypto_ids)}")
    return list(crypto_ids)


def get_cached_crypto_prices(crypto_ids: Optional[List[str]] = None, cache_duration: int = 300) -> Dict[str, float]:
    """Get cryptocurrency prices with caching to reduce API calls."""
    if crypto_ids is None:
        # Get all unique crypto IDs from accounts
        crypto_ids = get_crypto_ids_from_accounts(accounts)

    try:
        if os.path.exists(CRYPTO_CACHE_FILE):
            with open(CRYPTO_CACHE_FILE, 'r') as f:
                cache_data = json.load(f)
                timestamp = cache_data.get('timestamp', 0)
                current_time = time.time()

                # Check if cache is still valid
                if current_time - timestamp < cache_duration:
                    prices = cache_data.get('prices', {})

                    # Check if all requested crypto IDs are in the cache
                    missing_ids = [crypto_id for crypto_id in crypto_ids if crypto_id not in prices]

                    if not missing_ids:
                        logging.info(f"Using cached cryptocurrency prices (cached {int((current_time - timestamp) / 60)} minutes ago)")
                        for crypto_id, price in prices.items():
                            if crypto_id in crypto_ids:  # Only log the ones we're interested in
                                logging.info(f"Cached {crypto_id} price: ${price:,.2f}")
                        return prices
                    else:
                        logging.info(f"Cache missing prices for: {', '.join(missing_ids)}")
                else:
                    logging.info(f"Cache expired ({int((current_time - timestamp) / 60)} minutes old)")
    except Exception as e:
        logging.warning(f"Error reading cache: {e}")

    # Cache is invalid or doesn't exist, get fresh prices
    logging.info("Getting fresh cryptocurrency prices from API...")
    prices = get_crypto_prices(crypto_ids)

    # Update cache if we got valid prices
    if prices:
        try:
            cache_data = {
                'timestamp': time.time(),
                'prices': prices
            }

            # Ensure directory exists
            os.makedirs(os.path.dirname(CRYPTO_CACHE_FILE), exist_ok=True)

            with open(CRYPTO_CACHE_FILE, 'w') as f:
                json.dump(cache_data, f)
            logging.info("Updated cryptocurrency price cache")
        except Exception as e:
            logging.warning(f"Error writing cache: {e}")

    return prices


def get_stock_prices(symbols: List[str]) -> Dict[str, float]:
    """Get current prices for a list of stock symbols in USD."""
    try:
        logging.info(f"Fetching stock prices for: {symbols}")
        # Get stock data
        stock_data = yf.download(symbols, period="1d", progress=False)

        # Extract the most recent closing prices
        if len(symbols) == 1:
            # Handle single symbol case
            latest_price = stock_data['Close'].iloc[-1]
            prices = {symbols[0]: latest_price}
        else:
            # Handle multiple symbols
            latest_prices = stock_data['Close'].iloc[-1]
            prices = latest_prices.to_dict()

        # Log the prices
        for symbol, price in prices.items():
            logging.info(f"Current {symbol} price: ${price:.2f}")

        return prices
    except Exception as e:
        logging.error(f"Error fetching stock prices: {e}")
        return {}

def calculate_account_balances(accounts, crypto_prices, stock_prices, pl_api_key):
    """Calculate USD balance for each account based on crypto and stock holdings"""
    update_commands = []

    for account in accounts:
        total_usd = 0
        assets_summary = []

        # Process crypto assets if present
        if 'assets' in account and 'crypto' in account['assets']:
            crypto_assets = account['assets']['crypto']

            # Calculate value for each cryptocurrency
            for crypto_id, amount in crypto_assets.items():
                if crypto_id in crypto_prices and crypto_prices[crypto_id] is not None:
                    crypto_value = amount * crypto_prices[crypto_id]
                    if crypto_value > 0:
                        assets_summary.append(f"{crypto_id}: {amount} (${crypto_value:,.2f})")
                        total_usd += crypto_value
                else:
                    logging.warning(f"Price for {crypto_id} not found or is None")

        # Process stock assets if present
        if 'assets' in account and 'stock' in account['assets']:
            stock_assets = account['assets']['stock']

            for stock in stock_assets:
                symbol = stock['symbol']
                shares = stock['shares']

                if symbol in stock_prices:
                    stock_value = shares * stock_prices[symbol]
                    assets_summary.append(f"{symbol}: {shares} shares (${stock_value:,.2f})")
                    total_usd += stock_value
                else:
                    logging.warning(f"Price for {symbol} not found")

        # Format the command for ProjectionLab API
        command = f"window.projectionlabPluginAPI.updateAccount('{account['id']}', {{ balance: {total_usd:.2f} }}, {{ key: '{pl_api_key}' }})"
        update_commands.append(command)

        # Log account summary
        logging.info(f"Account: {account['name']}")
        for asset_summary in assets_summary:
            logging.info(f"  {asset_summary}")
        logging.info(f"  Total USD: ${total_usd:,.2f}")

    return update_commands


def handle_mfa_code(driver: webdriver.Chrome, mfa_code: str, pl_mfa: str, pl_email: str) -> bool:
    """Handle entering MFA code into split input fields for ProjectionLab."""
    logging.info(f"Entering MFA code: {mfa_code}")

    try:
        # Wait for the OTP input fields to be visible
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "v-otp-input__field"))
        )

        # Get all OTP input fields
        otp_inputs = driver.find_elements(By.CLASS_NAME, "v-otp-input__field")

        if len(otp_inputs) != len(mfa_code):
            logging.warning(f"Number of OTP input fields ({len(otp_inputs)}) doesn't match MFA code length ({len(mfa_code)})")

        # Clear all fields first
        for input_field in otp_inputs:
            input_field.clear()

        # Enter each digit one by one using direct Selenium methods
        for i, digit in enumerate(mfa_code):
            if i < len(otp_inputs):
                # Focus the field
                driver.execute_script("arguments[0].focus();", otp_inputs[i])
                # Clear it again to be sure
                otp_inputs[i].clear()
                # Send the digit
                otp_inputs[i].send_keys(digit)
                # Small delay between inputs
                time.sleep(0.2)

        # Find and click the submit button - try multiple approaches
        submit_button = None

        # Try finding by CSS selector first (most reliable)
        try:
            submit_button = driver.find_element(By.CSS_SELECTOR, ".app-card-actions button:first-child")
            logging.info("Found Submit button by CSS selector")
        except Exception:
            # Try finding by XPath
            try:
                submit_button = driver.find_element(By.XPATH, "//button[.//span[contains(text(), 'Submit')]]")
                logging.info("Found Submit button by XPath")
            except Exception:
                logging.warning("Could not find Submit button by CSS or XPath")

        if submit_button:
            # Wait a moment to ensure all digits are entered
            time.sleep(1)
            # Click the button
            driver.execute_script("arguments[0].click();", submit_button)
            logging.info("Clicked Submit button")
        else:
            # Try JavaScript click as last resort
            logging.info("Trying JavaScript click as last resort")
            js_click = """
            const submitBtn = Array.from(document.querySelectorAll('button')).find(btn =>
                btn.textContent.includes('Submit'));
            if (submitBtn) {
                submitBtn.click();
                return true;
            }
            return false;
            """
            click_result = driver.execute_script(js_click)
            logging.info(f"JavaScript click result: {click_result}")

        # Wait for the page to change
        time.sleep(5)

        # Check if we're still on the MFA page
        otp_fields_present = len(driver.find_elements(By.CLASS_NAME, "v-otp-input__field")) > 0

        if otp_fields_present:
            # Check if there's an error message
            error_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Invalid') or contains(text(), 'incorrect')]")
            if error_elements:
                error_text = error_elements[0].text
                logging.warning(f"Error message detected: {error_text}")

            logging.warning("Still on MFA page after submission, trying with a fresh TOTP code")

            # Get a fresh TOTP code
            try:
                fresh_mfa_code = get_totp_from_secret(pl_mfa, pl_email)

                if fresh_mfa_code and fresh_mfa_code != mfa_code:
                    logging.info(f"Got fresh TOTP code: {fresh_mfa_code}, trying again")
                    # Wait a moment before trying again
                    time.sleep(2)
                    # Try again with the fresh code
                    return handle_mfa_code(driver, fresh_mfa_code, pl_mfa, pl_email)
                else:
                    logging.warning("Could not get a different TOTP code")
            except Exception as totp_error:
                logging.error(f"Error getting fresh TOTP code: {totp_error}")

            return False

        logging.info("MFA code submitted successfully")
        return True

    except Exception as e:
        logging.error(f"Error handling MFA code: {e}")
        return False


def wait_for_login_completion(driver: webdriver.Chrome, timeout: int = 30) -> bool:
    """Wait for login to complete and verify we're on the main page."""
    logging.info("Waiting for login to complete...")

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Check if the ProjectionLab API is available
            api_available = driver.execute_script("return typeof window.projectionlabPluginAPI !== 'undefined';")
            if api_available:
                logging.info("ProjectionLab API is available, login successful")
                return True

            # Check if we're still on the login or MFA page
            login_elements = driver.find_elements(By.XPATH, '//*[@id="auth-container"]')
            mfa_elements = driver.find_elements(By.CLASS_NAME, "v-otp-input__field")

            if not login_elements and not mfa_elements:
                # We're not on login or MFA page, but API isn't available yet
                logging.info("Login/MFA pages no longer visible, waiting for API...")

            time.sleep(1)
        except Exception as e:
            logging.warning(f"Error while checking login status: {e}")
            time.sleep(1)

    logging.error(f"Timed out after {timeout} seconds waiting for login to complete")
    return False


def update_projectionlab(update_commands: List[str], config: Dict) -> bool:
    """Login to ProjectionLab and update account balances."""
    pl_config = config['projectionlab']
    projectionlab_url = pl_config['url']  # Use the same variable name as original
    pl_email = pl_config['username']
    pl_pass = pl_config['password']
    pl_mfa = pl_config.get('mfa_key')
    time_delay = pl_config['time_delay']

    # Debug check for MFA key
    if pl_mfa:
        if pl_mfa == '********' or '*' in pl_mfa:
            logging.error("ERROR: Using masked MFA key instead of actual key!")
            return False
        logging.info(f"MFA key is present (length: {len(pl_mfa)})")
    else:
        logging.info("No MFA key provided")

    logging.info(f"Starting ProjectionLab update with URL: {projectionlab_url}")

    # Create selenium browser
    logging.info("Initializing Selenium WebDriver...")
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')

    try:
        logging.info("Starting Chrome WebDriver...")
        # Specify the service with the path to chromedriver
        service = Service(executable_path='/usr/bin/chromedriver')
        driver = webdriver.Chrome(
            service=service,
            options=chrome_options
        )
        logging.info("WebDriver started successfully")
    except Exception as e:
        logging.error(f"Error starting Chrome WebDriver: {e}")
        return False

    try:
        logging.info(f"Navigating to ProjectionLab URL: {projectionlab_url}")
        driver.get(projectionlab_url)

        logging.info(f"Waiting {time_delay} seconds for the page to load.")
        time.sleep(time_delay)

        logging.info("Clicking Sign In with Email button...")
        sign_in_with_email_button = driver.find_element(By.XPATH, '//*[@id="auth-container"]/button[2]')
        driver.execute_script("arguments[0].click();", sign_in_with_email_button)
        time.sleep(1)

        logging.info("Entering email address...")
        try:
            email_input = driver.find_element(By.XPATH, '//*[@id="input-v-7"]')
        except:
            try:
                email_input = driver.find_element(By.XPATH, '//*[@id="input-v-6"]')
            except Exception as e:
                logging.error(f"Error finding email input: {e}")
                return False

        email_input.clear()
        email_input.send_keys(pl_email)
        time.sleep(1)

        logging.info("Entering password...")
        try:
            password_input = driver.find_element(By.XPATH, '//*[@id="input-v-9"]')
        except:
            try:
                password_input = driver.find_element(By.XPATH, '//*[@id="input-v-8"]')
            except Exception as e:
                logging.error(f"Error finding password input: {e}")
                return False

        password_input.clear()
        password_input.send_keys(pl_pass)
        time.sleep(1)

        logging.info("Clicking Sign button...")
        sign_in_button = driver.find_element(By.XPATH, '//*[@id="auth-container"]/form/button')
        driver.execute_script("arguments[0].click();", sign_in_button)
        time.sleep(3)

        # Generate TOTP code from secret if MFA is configured
        mfa_code = None
        if pl_mfa:
            mfa_code = get_totp_from_secret(pl_mfa, pl_email)
            logging.info(f"Generated TOTP code for MFA: {mfa_code}")

        # Check if MFA is required by looking for OTP input fields
        mfa_handled = False
        if pl_mfa:
            try:
                # Check if the OTP input fields are present
                otp_fields_present = len(driver.find_elements(By.CLASS_NAME, "v-otp-input__field")) > 0

                if otp_fields_present:
                    logging.info("MFA page detected")

                    # Get a fresh TOTP code right before using it
                    if not mfa_code:
                        try:
                            mfa_code = get_totp_from_secret(pl_mfa, pl_email)
                            logging.info(f"Retrieved fresh TOTP code for MFA: {mfa_code}")
                        except Exception as totp_error:
                            logging.error(f"Error getting fresh TOTP code: {totp_error}")

                    if mfa_code:
                        # Use the fresh code
                        mfa_handled = handle_mfa_code(driver, mfa_code, pl_mfa, pl_email)
                    else:
                        logging.error("No MFA code available")
                else:
                    logging.info("MFA page not detected, continuing with login flow")
                    mfa_handled = True
            except Exception as e:
                logging.warning(f"Error checking for MFA page: {e}")

        # Wait for login to complete with a more robust method
        login_successful = wait_for_login_completion(driver, timeout=30)

        if not login_successful:
            logging.error("Failed to complete login process.")
            try:
                # Get the current URL and page source for debugging
                current_url = driver.current_url
                logging.info(f"Current URL: {current_url}")

                # Print a small portion of the page source to avoid log flooding
                page_source = driver.page_source[:500] + "..." if len(driver.page_source) > 500 else driver.page_source
                logging.info(f"Page source snippet: {page_source}")
            except Exception as ss_error:
                logging.error(f"Error debugging: {ss_error}")

            logging.error("Exiting due to login failure")
            return False

        logging.info("Login completed successfully")

        # Continue waiting for the main page to load
        logging.info(f"Waiting until ProjectionLab API becomes available...")
        WebDriverWait(driver, 20).until(
            lambda d: d.execute_script("return typeof window.projectionlabPluginAPI !== 'undefined';")
        )

        logging.info("Updating accounts in ProjectionLab...")
        for command in update_commands:
            # Redact API key in logs
            redacted_command = command.replace(command.split("key: '")[1].split("'")[0], "***REDACTED***")
            logging.info(f"Executing command: {redacted_command}")
            driver.execute_script(command)
            logging.info("Successfully executed command. Sleeping 1 sec")
            time.sleep(1)

        logging.info(f"All updates completed successfully. Waiting {time_delay} seconds before quit...")
        time.sleep(time_delay)
        return True

    except Exception as e:
        logging.error(f"Error updating ProjectionLab: {e}")
        return False
    finally:
        logging.info("Closing WebDriver.")
        driver.quit()


def main() -> None:
    """Main function to run the script."""
    try:
        logging.info("Starting ProjectionLab asset update script")

        # Load configuration from environment variables
        logging.info("Loading configuration from environment variables...")
        config = get_config_from_env()
        if not config:
            sys.exit(1)

        # Load accounts
        logging.info("Loading accounts configuration...")
        accounts_data = load_yaml(ACCOUNTS_PATH)

        # Get accounts
        global accounts  # Make accounts global so it can be accessed by get_crypto_ids_from_accounts
        accounts = accounts_data.get('accounts', [])
        logging.info(f"Loaded {len(accounts)} accounts from configuration")

        # If in validation mode, exit here
        if VALIDATE_ONLY:
            logging.info("Validation mode: Configuration loaded successfully")
            return

        # Try to obtain a lock
        if not obtain_lock():
            sys.exit(0)  # Exit cleanly if we couldn't get the lock

        try:
            # Get API key
            pl_api_key = config.get('projectionlab', {}).get('api_key')
            if not pl_api_key:
                logging.error("ProjectionLab API key not found in configuration")
                sys.exit(1)

            # Get all unique crypto IDs from accounts
            crypto_ids = get_crypto_ids_from_accounts(accounts)

            # Get current crypto prices (using cache)
            logging.info("Fetching current cryptocurrency prices...")
            # Set cache to 5 minutes
            cache_duration = 300
            crypto_prices = get_cached_crypto_prices(crypto_ids, cache_duration=cache_duration)

            if not crypto_prices:
                logging.error("Failed to fetch cryptocurrency prices. Exiting...")
                sys.exit(1)

            # Collect all stock symbols from accounts
            stock_symbols = []
            for account in accounts:
                if 'assets' in account and 'stock' in account['assets']:
                    for stock in account['assets']['stock']:
                        stock_symbols.append(stock['symbol'])

            # Get stock prices if there are any stock symbols
            stock_prices = {}
            if stock_symbols:
                stock_prices = get_stock_prices(stock_symbols)

            # Calculate account balances and generate update commands
            logging.info("Calculating account balances...")
            update_commands = calculate_account_balances(accounts, crypto_prices, stock_prices, pl_api_key)

            # Update ProjectionLab (can be commented out for testing)
            should_update_projectionlab = os.getenv('UPDATE_PROJECTIONLAB', 'true').lower() == 'true'

            if should_update_projectionlab:
                logging.info("Updating ProjectionLab with calculated balances...")
                success = update_projectionlab(update_commands, config)
                if success:
                    logging.info("ProjectionLab update completed successfully")
                else:
                    logging.error("ProjectionLab update failed")
                    sys.exit(1)
            else:
                logging.info("Skipping ProjectionLab update (UPDATE_PROJECTIONLAB=false)")

            logging.info("Script completed successfully")
        finally:
            release_lock()
    except Exception as e:
        logging.error(f"Fatal error in main: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
