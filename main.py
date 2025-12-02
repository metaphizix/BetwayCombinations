"""
Main entry point for Betway automation
"""
import asyncio
import os
import json
import random
import math
import gc  # Garbage collection for memory management
from itertools import product
from playwright.async_api import async_playwright, Page
from playwright._impl._errors import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re

# Load environment variables from .env file
load_dotenv()


# ============================================================================
# BET VERIFICATION HELPER FUNCTIONS
# ============================================================================

async def get_current_balance(page: Page) -> float:
    """
    Get the current account balance from the page.
    Returns the balance as a float, or -1.0 if unable to retrieve.
    """
    try:
        # Try multiple selectors for balance
        balance_selectors = [
            '#header-balance',
            'strong:has-text("Balance")',
            'span:has-text("R ")',
            'div[class*="balance"]',
        ]
        
        for selector in balance_selectors:
            try:
                element = await page.query_selector(selector)
                if element and await element.is_visible():
                    if selector == 'strong:has-text("Balance")':
                        # Get parent div for full balance text
                        parent = await element.evaluate_handle('el => el.closest("div")')
                        text = await parent.inner_text()
                    else:
                        text = await element.inner_text()
                    
                    # Extract numeric value using regex (e.g., "R 85.51" -> 85.51)
                    match = re.search(r'R\s*(\d+(?:[.,]\d+)?)', text)
                    if match:
                        balance_str = match.group(1).replace(',', '.')
                        return float(balance_str)
            except:
                continue
        
        return -1.0  # Unable to get balance
    except Exception as e:
        print(f"    ⚠️ Error getting balance: {e}")
        return -1.0


async def count_betslip_selections(page: Page) -> int:
    """
    Count the number of selections currently in the bet slip.
    Returns the count, or -1 if unable to determine.
    """
    try:
        betslip = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
        if not betslip:
            return 0
        
        betslip_text = await betslip.inner_text()
        
        # Count "1X2" occurrences (each selection shows its market type)
        count_1x2 = betslip_text.count('1X2')
        
        # Alternative: count selection entries by looking for odds patterns
        # Each selection shows odds like "@ 1.85" or similar
        odds_matches = re.findall(r'@\s*\d+\.\d{2}', betslip_text)
        count_odds = len(odds_matches)
        
        # Return the higher count (more reliable)
        return max(count_1x2, count_odds)
    except Exception as e:
        print(f"    ⚠️ Error counting selections: {e}")
        return -1


async def get_betslip_id_from_confirmation(page: Page) -> dict:
    """
    Extract the Betslip ID and/or Booking Code from the Bet Confirmation modal.
    Returns a dict with 'betslip_id' and 'booking_code' (either may be empty string).
    """
    result = {'betslip_id': '', 'booking_code': ''}
    
    try:
        # Wait a moment for modal to fully render
        await page.wait_for_timeout(800)
        
        # PRIORITY 1: Look specifically for Bet Confirmation modal elements
        # The modal typically contains "Bet Confirmation" header and the Booking Code
        bet_confirmation_selectors = [
            'span:has-text("Bet Confirmation")',  # Modal header
            'div:has(span:has-text("Bet Confirmation"))',  # Container with header
            'div[class*="modal"]:has(button#strike-conf-continue-btn)',  # Modal with continue button
        ]
        
        modal_text = ""
        
        # First, try to find the specific Bet Confirmation modal
        for selector in bet_confirmation_selectors:
            try:
                element = await page.query_selector(selector)
                if element:
                    # Get the parent modal container for full text
                    try:
                        parent = await element.evaluate_handle('el => el.closest("div[class*=modal]") || el.closest("div[role=dialog]") || el.parentElement.parentElement.parentElement')
                        if parent:
                            modal_text = await parent.inner_text()
                    except:
                        modal_text = await element.inner_text()
                    
                    if modal_text and 'Bet Confirmation' in modal_text:
                        print(f"    ✓ Found Bet Confirmation modal")
                        break
            except:
                continue
        
        # PRIORITY 2: Look for the Continue betting button and get its modal container
        if not modal_text or len(modal_text) < 20:
            try:
                continue_btn = await page.query_selector('button#strike-conf-continue-btn')
                if continue_btn:
                    # Navigate up to find the modal container
                    parent = await continue_btn.evaluate_handle('''el => {
                        let current = el.parentElement;
                        for (let i = 0; i < 10 && current; i++) {
                            if (current.className && (current.className.includes('modal') || current.className.includes('dialog'))) {
                                return current;
                            }
                            current = current.parentElement;
                        }
                        return el.parentElement.parentElement.parentElement.parentElement;
                    }''')
                    if parent:
                        modal_text = await parent.inner_text()
            except:
                pass
        
        if modal_text:
            # Debug: print modal text to identify patterns
            print(f"    🔍 [DEBUG] Bet Confirmation modal text:")
            # Print in chunks to see the full structure
            lines = modal_text.split('\n')
            for i, line in enumerate(lines[:15]):  # First 15 lines
                if line.strip():
                    print(f"         Line {i+1}: {line.strip()[:80]}")
            
            # Pattern 1: Look for Booking Code (most common in Betway)
            # Betway typically shows "Booking Code" followed by the code
            booking_patterns = [
                r'Booking\s*Code[\s:]*\n?\s*([A-Z0-9-]+)',  # Code may be on next line
                r'Booking\s*Code[\s:]+([A-Z0-9-]+)',
                r'Booking[\s:]+([A-Z0-9]{6,})',
                r'Code[\s:]+([A-Z0-9]{8,})',  # At least 8 chars
            ]
            
            for pattern in booking_patterns:
                match = re.search(pattern, modal_text, re.IGNORECASE)
                if match:
                    result['booking_code'] = match.group(1).strip()
                    print(f"    ✓ [FOUND] Booking Code: {result['booking_code']}")
                    break
            
            # Pattern 2: Look for Betslip ID
            betslip_patterns = [
                r'Betslip\s*ID[\s:]*\n?\s*([A-Z0-9-]+)',
                r'Betslip\s*ID[\s:]+([A-Z0-9-]+)',
                r'Bet\s*ID[\s:]+([A-Z0-9-]+)',
                r'Reference[\s:]+([A-Z0-9-]+)',
            ]
            
            for pattern in betslip_patterns:
                match = re.search(pattern, modal_text, re.IGNORECASE)
                if match:
                    result['betslip_id'] = match.group(1).strip()
                    print(f"    ✓ [FOUND] Betslip ID: {result['betslip_id']}")
                    break
            
            # Pattern 3: Look for standalone alphanumeric codes if nothing found yet
            # Betway booking codes are typically 8-12 uppercase alphanumeric
            if not result['booking_code'] and not result['betslip_id']:
                # Look for a line that's just a code (common pattern)
                for line in lines:
                    line = line.strip()
                    # Check if line is a standalone code (8-12 alphanumeric chars)
                    if re.match(r'^[A-Z0-9]{8,12}$', line):
                        if line not in ['BETCONFIRM', 'SUCCESSFUL', 'CONTINUING']:
                            result['booking_code'] = line
                            print(f"    ✓ [FOUND] Standalone Code: {result['booking_code']}")
                            break
        else:
            print(f"    ⚠️ Could not find Bet Confirmation modal text")
        
        return result
        
    except Exception as e:
        print(f"    ⚠️ Error getting betslip ID/booking code: {e}")
        return result


async def verify_bet_placement(page: Page, expected_stake: float, balance_before: float) -> dict:
    """
    Verify that a bet was actually placed by checking multiple indicators.
    
    Returns a dict with:
        - 'success': True if bet was verified placed
        - 'betslip_id': The betslip ID if found
        - 'booking_code': The booking code if found
        - 'balance_after': The balance after the bet
        - 'balance_decreased': True if balance decreased by expected amount
        - 'confidence': 'HIGH', 'MEDIUM', or 'LOW' based on verification checks
    """
    result = {
        'success': False,
        'betslip_id': '',
        'booking_code': '',
        'balance_after': -1.0,
        'balance_decreased': False,
        'confidence': 'LOW'
    }
    
    try:
        # 1. Try to get the Betslip ID and Booking Code from confirmation
        codes = await get_betslip_id_from_confirmation(page)
        if codes['betslip_id']:
            result['betslip_id'] = codes['betslip_id']
            print(f"    ✓ [VERIFY] Found Betslip ID: {codes['betslip_id']}")
        if codes['booking_code']:
            result['booking_code'] = codes['booking_code']
            print(f"    ✓ [VERIFY] Found Booking Code: {codes['booking_code']}")
        
        has_code = bool(result['betslip_id'] or result['booking_code'])
        
        # 2. Check balance after bet
        await page.wait_for_timeout(1000)  # Wait for balance to update
        balance_after = await get_current_balance(page)
        result['balance_after'] = balance_after
        
        if balance_before > 0 and balance_after > 0:
            expected_decrease = expected_stake
            actual_decrease = balance_before - balance_after
            
            # Allow for small variance (rounding)
            if abs(actual_decrease - expected_decrease) < 0.10:
                result['balance_decreased'] = True
                print(f"    ✓ [VERIFY] Balance decreased correctly: R{balance_before:.2f} → R{balance_after:.2f} (stake: R{expected_stake:.2f})")
            elif actual_decrease > 0:
                print(f"    ⚠️ [VERIFY] Balance decreased but amount differs: expected R{expected_stake:.2f}, actual R{actual_decrease:.2f}")
                result['balance_decreased'] = True  # Still counts as placed
            else:
                print(f"    ❌ [VERIFY] Balance did NOT decrease: R{balance_before:.2f} → R{balance_after:.2f}")
        
        # 3. Check for "Continue betting" button (indicates success modal is showing)
        continue_btn = await page.query_selector('button#strike-conf-continue-btn')
        has_continue_btn = continue_btn and await continue_btn.is_visible()
        
        # 4. Check for "Bet Confirmation" text
        bet_conf = await page.query_selector('span:has-text("Bet Confirmation")')
        has_bet_conf = bet_conf is not None
        
        # Determine confidence and success
        # HIGH: Both code AND balance verification
        if has_code and result['balance_decreased']:
            result['confidence'] = 'HIGH'
            result['success'] = True
        # HIGH: Balance verified AND confirmation modal visible
        elif result['balance_decreased'] and (has_continue_btn or has_bet_conf):
            result['confidence'] = 'HIGH'
            result['success'] = True
        # MEDIUM: Either code OR balance verification
        elif has_code or result['balance_decreased']:
            result['confidence'] = 'MEDIUM'
            result['success'] = True
        # MEDIUM: Both confirmation elements present
        elif has_continue_btn and has_bet_conf:
            result['confidence'] = 'MEDIUM'
            result['success'] = True
        # LOW: Only one confirmation element
        elif has_continue_btn or has_bet_conf:
            result['confidence'] = 'LOW'
            result['success'] = True  # Assume success but low confidence
            print(f"    ⚠️ [VERIFY] LOW confidence - only found confirmation modal, no balance/code verification")
        
        return result
        
    except Exception as e:
        print(f"    ⚠️ [VERIFY] Error during verification: {e}")
        return result


async def verify_selections_before_bet(page: Page, expected_count: int) -> bool:
    """
    Verify the correct number of selections are in the bet slip before placing bet.
    Returns True if count matches expected, False otherwise.
    """
    try:
        actual_count = await count_betslip_selections(page)
        
        if actual_count == expected_count:
            print(f"    ✓ [PRE-BET] Correct selections: {actual_count}/{expected_count}")
            return True
        elif actual_count > expected_count:
            print(f"    ❌ [PRE-BET] Too many selections: {actual_count}/{expected_count} - betslip not cleared properly!")
            return False
        else:
            print(f"    ❌ [PRE-BET] Missing selections: {actual_count}/{expected_count}")
            return False
    except Exception as e:
        print(f"    ⚠️ [PRE-BET] Error verifying selections: {e}")
        return True  # Continue if we can't verify


# ============================================================================
# END BET VERIFICATION HELPER FUNCTIONS
# ============================================================================


async def retry_with_backoff(func, max_retries=3, initial_delay=5, **kwargs):
    """
    Retry a function with exponential backoff on network/timeout errors
    """
    for attempt in range(max_retries):
        try:
            # If kwargs are provided, pass them; otherwise call function without args
            if kwargs:
                return await func(**kwargs)
            else:
                return await func()
        except (PlaywrightError, PlaywrightTimeoutError, Exception) as e:
            error_msg = str(e).lower()
            # Check if it's a network/timeout error
            is_network_error = any(keyword in error_msg for keyword in [
                'err_name_not_resolved', 'err_connection', 'err_internet_disconnected',
                'timeout', 'net::', 'connection', 'network'
            ])
            
            if is_network_error and attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)  # Exponential backoff
                print(f"\n[NETWORK ERROR] {type(e).__name__}: {e}")
                print(f"[RETRY] Attempt {attempt + 1}/{max_retries} - Waiting {delay}s before retry...")
                await asyncio.sleep(delay)
            else:
                raise

async def login_to_betway(playwright):
    """Login to Betway using credentials from .env file"""
    
    # Get credentials from environment variables
    username = os.getenv('BETWAY_USERNAME')
    password = os.getenv('BETWAY_PASSWORD')
    
    if not username or not password:
        print("Error: BETWAY_USERNAME or BETWAY_PASSWORD not found in .env file")
        return None
    
    # Launch browser with memory optimization args (set headless=False to see the browser)
    browser = await playwright.chromium.launch(
        headless=False,
        args=[
            '--disable-dev-shm-usage',  # Use /tmp instead of /dev/shm (prevents memory issues)
            '--no-sandbox',
            '--disable-gpu',
            '--disable-software-rasterizer',
            '--disable-extensions',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--js-flags=--max-old-space-size=4096',  # Increase V8 heap to 4GB
        ]
    )
    page = await browser.new_page()
    
    print("Navigating to Betway...")
    
    # Retry navigation with exponential backoff on network errors
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await page.goto('https://new.betway.co.za/sport/soccer', timeout=30000)
            break
        except Exception as e:
            error_msg = str(e).lower()
            is_network_error = any(keyword in error_msg for keyword in [
                'err_name_not_resolved', 'err_connection', 'err_internet_disconnected',
                'timeout', 'net::', 'connection', 'network'
            ])
            
            if is_network_error and attempt < max_retries - 1:
                delay = 5 * (2 ** attempt)
                print(f"[NETWORK ERROR] {e}")
                print(f"[RETRY] Attempt {attempt + 1}/{max_retries} - Waiting {delay}s...")
                await asyncio.sleep(delay)
            else:
                print(f"[FATAL] Could not connect to Betway after {max_retries} attempts")
                await browser.close()
                raise
    
    # Wait for page to load
    await page.wait_for_timeout(3000)
    
    # Try multiple selectors to open login modal
    print("Opening login modal...")
    login_opened = False
    
    # Try different selectors for the login button
    selectors = [
        '#header-username',
        'button:has-text("Log In")',
        'a:has-text("Log In")',
        'button:has-text("Login")',
        'a:has-text("Login")',
        '[data-testid="login-button"]',
        '.login-button',
        'button[class*="login"]',
        'a[class*="login"]',
        'div[class*="login"]',
        'button[id*="login"]',
        'a[href*="login"]',
        # Try looking for user/account icons
        'svg[class*="user"]',
        'button[aria-label*="Log"]',
        'button[aria-label*="Sign"]',
        # Look for any visible button in header area
        'header button',
        'nav button',
    ]
    
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, timeout=2000)
            if element and await element.is_visible():
                text = await element.inner_text() if await element.evaluate('el => el.tagName') != 'svg' else ''
                await element.click()
                login_opened = True
                print(f"  ✓ Clicked login using selector: {selector} (text: '{text}')")
                break
        except:
            continue
    
    if not login_opened:
        print("ERROR: Could not find login button with any known selector")
        print("Page title:", await page.title())
        print("Attempting to capture page state...")
        
        # Try to get all buttons on the page for debugging
        try:
            all_buttons = await page.query_selector_all('button')
            print(f"\nFound {len(all_buttons)} buttons on page. First 10:")
            for i, btn in enumerate(all_buttons[:10]):
                try:
                    text = await btn.inner_text()
                    classes = await btn.get_attribute('class')
                    btn_id = await btn.get_attribute('id')
                    print(f"  Button {i+1}: text='{text[:30]}', id='{btn_id}', class='{classes[:50] if classes else ''}'")
                except:
                    pass
        except:
            pass
        
        await browser.close()
        return None
    
    # Wait for modal to appear
    await page.wait_for_timeout(1500)
    
    # Find and fill the modal fields
    print(f"Filling username: {username}")
    modal_username = await page.wait_for_selector('input[placeholder="Mobile Number"]', timeout=5000)
    await modal_username.fill(username)
    
    print("Filling password...")
    modal_password = await page.query_selector('input[placeholder="Enter Password"]')
    await modal_password.fill(password)
    
    print("Submitting login form...")
    await modal_password.press('Enter')
    
    print("Waiting for authentication...")
    
    # Check if login was successful by looking for the balance
    print("Verifying login...")
    
    max_attempts = 10
    attempt = 0
    login_successful = False
    
    while attempt < max_attempts and not login_successful:
        attempt += 1
        
        try:
            balance_element = await page.wait_for_selector('strong:has-text("Balance")', timeout=3000)
            if balance_element:
                parent = await balance_element.evaluate_handle('el => el.closest("div")')
                balance_text = await parent.inner_text()
                # Format balance text to be on one line
                balance_clean = balance_text.replace('\n', ' ').strip()
                print(f"[OK] Login successful! {balance_clean}")
                login_successful = True
                break
        except Exception:
            if attempt < max_attempts:
                await page.wait_for_timeout(2000)
    
    if not login_successful:
        print("! Could not verify login. Please check the browser.")
        await browser.close()
        return None
    
    # Close any welcome modals/popups after login
    print("Checking for post-login modals/popups...")
    close_selectors = [
        'button:has-text("×")',
        'button:has-text("GOT IT")',
        'button[aria-label="Close"]',
        'svg[id="modal-close-btn"]',
    ]
    
    for selector in close_selectors:
        try:
            close_btns = await page.query_selector_all(selector)
            for btn in close_btns:
                if await btn.is_visible():
                    # Skip buttons related to account/deposit
                    try:
                        btn_text = await btn.inner_text()
                        btn_aria = await btn.get_attribute('aria-label') or ''
                        combined = f"{btn_text} {btn_aria}".lower()
                        if any(kw in combined for kw in ['deposit', 'account', 'profile', 'login']):
                            continue
                    except:
                        pass
                    
                    await btn.evaluate('el => el.click()')
                    await page.wait_for_timeout(500)
                    print("  Closed post-login modal/popup")
                    break
        except:
            pass
    
    # Return the page and browser objects so they can be used for betting
    return {
        "page": page,
        "browser": browser
    }


async def restart_browser_fresh(playwright, old_browser=None, old_page=None):
    """
    Create a completely fresh browser instance to prevent Playwright memory corruption.
    
    This is the TRUE fix for Playwright memory errors like:
    - AttributeError: 'dict' object has no attribute '_object'
    - Frame object corruption
    - Element handle collection errors
    
    The function:
    1. Closes the old browser instance completely
    2. Forces garbage collection
    3. Creates a brand new browser with fresh memory state
    4. Logs in again
    5. Returns the new page and browser objects
    
    Args:
        playwright: The playwright instance
        old_browser: The old browser to close (optional)
        old_page: The old page to close (optional)
    
    Returns:
        dict with 'page' and 'browser' keys, or None on failure
    """
    print(f"\n{'='*60}")
    print("🔄 BROWSER RESTART - Creating fresh browser instance")
    print(f"{'='*60}")
    print("   This prevents Playwright memory corruption errors")
    print("   All internal state will be reset")
    print(f"{'='*60}\n")
    
    # Step 1: Close old browser if provided
    if old_page or old_browser:
        print("  [1/4] Closing old browser instance...")
        try:
            if old_page and not old_page.is_closed():
                await old_page.close()
        except Exception as e:
            print(f"       Warning: Could not close old page: {e}")
        
        try:
            if old_browser:
                await old_browser.close()
        except Exception as e:
            print(f"       Warning: Could not close old browser: {e}")
        
        print("       ✓ Old browser closed")
    
    # Step 2: Force garbage collection to release memory
    print("  [2/4] Running garbage collection...")
    gc.collect()
    gc.collect()  # Run twice to ensure full collection
    print("       ✓ Memory cleaned up")
    
    # Step 3: Wait a moment for resources to be released
    print("  [3/4] Waiting for resources to release...")
    await asyncio.sleep(2)
    print("       ✓ Ready to create new browser")
    
    # Step 4: Create fresh browser and login
    print("  [4/4] Creating fresh browser and logging in...")
    try:
        result = await login_to_betway(playwright)
        if result:
            print(f"\n{'='*60}")
            print("✅ BROWSER RESTART COMPLETE")
            print(f"{'='*60}")
            print("   Fresh browser instance created")
            print("   All Playwright state reset")
            print("   Ready to continue betting")
            print(f"{'='*60}\n")
            return result
        else:
            print("  ❌ Failed to login with new browser")
            return None
    except Exception as e:
        print(f"  ❌ Error creating new browser: {e}")
        return None


async def check_and_relogin(page: Page, browser) -> bool:
    """
    Check if we're still logged in. If not, re-login.
    Returns True if logged in (or successfully re-logged in), False if re-login failed.
    """
    try:
        # Check for balance element (indicates logged in)
        balance_element = await page.query_selector('strong:has-text("Balance")')
        if balance_element and await balance_element.is_visible():
            return True  # Still logged in
        
        # Also check for username/balance in header
        header_balance = await page.query_selector('#header-balance')
        if header_balance:
            text = await header_balance.inner_text()
            if 'R' in text or 'Balance' in text:
                return True  # Still logged in
        
        # Check if betslip shows "Login" button (indicates logged out)
        betslip = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
        if betslip:
            betslip_text = await betslip.inner_text()
            if 'Login' in betslip_text and 'Bet Now' not in betslip_text:
                print("\n⚠️ [SESSION EXPIRED] Detected logged out state - re-logging in...")
            else:
                # Might still be logged in, just can't verify
                return True
        
        # If we got here, we need to re-login
        print("🔄 [RE-LOGIN] Attempting to re-authenticate...")
        
        # Get credentials from env
        username = os.getenv('BETWAY_USERNAME')
        password = os.getenv('BETWAY_PASSWORD')
        
        if not username or not password:
            print("❌ [RE-LOGIN FAILED] Credentials not found in .env")
            return False
        
        # Navigate to the main page to trigger login
        try:
            await page.goto('https://new.betway.co.za/sport/soccer', timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"❌ [RE-LOGIN FAILED] Could not navigate: {e}")
            return False
        
        # Try to find and click login button
        login_selectors = [
            '#header-username',
            'button:has-text("Log In")',
            'a:has-text("Log In")',
            'button:has-text("Login")',
        ]
        
        login_opened = False
        for selector in login_selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=2000)
                if element and await element.is_visible():
                    await element.click()
                    login_opened = True
                    print(f"  ✓ Clicked login button: {selector}")
                    break
            except:
                continue
        
        if not login_opened:
            # Check if we're actually already logged in (balance visible)
            try:
                balance = await page.query_selector('strong:has-text("Balance")')
                if balance and await balance.is_visible():
                    print("  ✓ Already logged in!")
                    return True
            except:
                pass
            print("❌ [RE-LOGIN FAILED] Could not find login button")
            return False
        
        # Wait for login modal
        await page.wait_for_timeout(1500)
        
        # Fill credentials
        try:
            modal_username = await page.wait_for_selector('input[placeholder="Mobile Number"]', timeout=5000)
            await modal_username.fill(username)
            
            modal_password = await page.query_selector('input[placeholder="Enter Password"]')
            await modal_password.fill(password)
            
            await modal_password.press('Enter')
            print("  ✓ Credentials submitted")
        except Exception as e:
            print(f"❌ [RE-LOGIN FAILED] Could not fill credentials: {e}")
            return False
        
        # Wait and verify login
        await page.wait_for_timeout(3000)
        
        for attempt in range(5):
            try:
                balance_element = await page.wait_for_selector('strong:has-text("Balance")', timeout=2000)
                if balance_element:
                    parent = await balance_element.evaluate_handle('el => el.closest("div")')
                    balance_text = await parent.inner_text()
                    balance_clean = balance_text.replace('\n', ' ').strip()
                    print(f"✅ [RE-LOGIN SUCCESS] {balance_clean}")
                    
                    # Close any post-login modals
                    await close_all_modals(page, max_attempts=2)
                    return True
            except:
                if attempt < 4:
                    await page.wait_for_timeout(1000)
        
        print("❌ [RE-LOGIN FAILED] Could not verify login after re-authentication")
        return False
        
    except Exception as e:
        print(f"❌ [RE-LOGIN ERROR] {e}")
        return False

async def close_all_modals(page: Page, max_attempts=3):
    """
    Aggressively attempt to close all modals/popups that might appear.
    Tries multiple times with various selectors, including betslip modal.
    IMPORTANT: Avoids clicking on account/profile related elements.
    """
    for attempt in range(max_attempts):
        try:
            # FIRST: Check for and close Account Options modal (Deposit funds tab)
            # This modal sometimes appears unexpectedly
            try:
                account_modal_indicators = [
                    'text="Account Options"',
                    'text="Deposit funds"',
                    'text="27614220968"',  # Account number pattern
                    ':has-text("Account Options")',
                ]
                for indicator in account_modal_indicators:
                    try:
                        modal_elem = await page.query_selector(indicator)
                        if modal_elem and await modal_elem.is_visible():
                            print(f"    ⚠️ Detected Account Options modal - closing...")
                            # Press Escape to close modal
                            await page.keyboard.press('Escape')
                            await asyncio.sleep(0.5)
                            
                            # Also try clicking outside the modal or close button
                            close_btns = await page.query_selector_all('svg[id="modal-close-btn"], button[aria-label="Close"]')
                            for close_btn in close_btns:
                                if await close_btn.is_visible():
                                    await close_btn.click()
                                    await asyncio.sleep(0.3)
                                    break
                            break
                    except:
                        continue
            except:
                pass
            
            # Try various close button selectors (including betslip close from HTML)
            # IMPORTANT: These selectors are specific to CLOSE buttons only, not action buttons
            close_selectors = [
                'svg[id="modal-close-btn"]',  # Betslip and modal close button (specific ID)
                'button[aria-label="Close"]',  # Exact match for Close button
                'button[aria-label="close"]',  # Exact match lowercase
                'button:has-text("×")',  # X button
                'button:has-text("Close"):not([aria-label*="Account"])',  # Close text but not account related
                'button:has-text("GOT IT")',  # Common popup dismiss
                'button:has-text("OK"):not([id*="deposit"]):not([id*="account"])',  # OK button but not deposit/account
            ]
            
            closed_any = False
            for selector in close_selectors:
                try:
                    close_buttons = await page.query_selector_all(selector)
                    for btn in close_buttons:
                        if await btn.is_visible():
                            # CRITICAL: Skip buttons that might be account/deposit related
                            try:
                                btn_text = await btn.inner_text()
                                btn_aria = await btn.get_attribute('aria-label') or ''
                                btn_id = await btn.get_attribute('id') or ''
                                
                                # Skip if this looks like an account/deposit/profile button
                                skip_keywords = ['deposit', 'account', 'profile', 'login', 'sign', 'register', 'withdraw']
                                combined_text = f"{btn_text} {btn_aria} {btn_id}".lower()
                                if any(keyword in combined_text for keyword in skip_keywords):
                                    continue
                            except:
                                pass
                            
                            await btn.click()
                            closed_any = True
                            await asyncio.sleep(0.3)
                except Exception:
                    continue
            
            # Try Escape key as well (safest way to close modals)
            try:
                await page.keyboard.press('Escape')
                await asyncio.sleep(0.3)
            except:
                pass
            
            # If we didn't close anything, we're done
            if not closed_any:
                break
                
            # Wait a bit before next attempt
            await asyncio.sleep(0.5)
                    
        except Exception as e:
            # Modals might not be present, that's okay
            pass

async def scrape_matches(page: Page, num_matches: int):
    """Scrape available matches and their betting options
    
    Scrapes Premier League matches from the Full-Time Result market.
    Automatically handles pagination by clicking 'Next' button to load more matches.
    Searches up to 20 pages to find required matches and caches their URLs.
    """
    print(f"\nScraping {num_matches} matches (searching up to 20 pages)...")
    
    await close_all_modals(page)
    await page.wait_for_timeout(1500)
    
    matches = []
    max_pages = 20
    current_page = 1
    
    try:
        while len(matches) < num_matches and current_page <= max_pages:
            print(f"\nSearching page {current_page}...")
            
            # Scroll to load content
            for _ in range(3):
                await page.evaluate('window.scrollBy(0, 500)')
                await page.wait_for_timeout(200)
            
            match_containers = await page.query_selector_all('div[data-v-206d232b].relative.grid.grid-cols-12')
            
            print(f"Found {len(match_containers)} matches on page {current_page}")
            
            for i, container in enumerate(match_containers):
                if len(matches) >= num_matches:
                    break
                    
                try:
                    team_elements = await container.query_selector_all('strong.overflow-hidden.text-ellipsis')
                    if len(team_elements) >= 2:
                        team1 = await team_elements[0].inner_text()
                        team2 = await team_elements[1].inner_text()
                    else:
                        continue
                    
                    start_time = None
                    try:
                        all_spans = await container.query_selector_all('span')
                        for span in all_spans:
                            try:
                                span_text = await span.inner_text()
                                if span_text and (
                                    re.match(r'(Today|Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun).*\d{1,2}:\d{2}', span_text) or
                                    re.match(r'\d{1,2}\s+\w{3}\s*-\s*\d{1,2}:\d{2}', span_text)
                                ):
                                    start_time = span_text.strip()
                                    break
                            except:
                                continue
                    except:
                        pass
                    
                    all_price_divs = await container.query_selector_all('div[price]')
                    odds = []
                    
                    if len(all_price_divs) >= 3:
                        for j in range(3):
                            btn = all_price_divs[j]
                            try:
                                odd_elem = await btn.query_selector('span')
                                if odd_elem:
                                    odd_text = await odd_elem.inner_text()
                                    if odd_text and odd_text.replace('.', '').replace(',', '').isdigit():
                                        odds.append(float(odd_text.replace(',', '.')))
                            except:
                                continue
                    
                    # Get match URL by clicking the team names div
                    match_url = None
                    try:
                        # Find the clickable div with team names
                        team_div = await container.query_selector('div.flex.flex-row.w-full.gap-1.pr-2')
                        if team_div:
                            # Click to navigate to match page
                            await team_div.click()
                            await page.wait_for_timeout(1000)
                            
                            # Get the URL
                            match_url = page.url
                            print(f"    ✓ Captured URL: {match_url}")
                            
                            # Go back to matches list
                            await page.go_back()
                            await page.wait_for_timeout(1000)
                            await close_all_modals(page)
                    except Exception as e:
                        print(f"    ⚠️  Could not capture URL: {e}")
                    
                    if len(odds) == 3:
                        match = {
                            "name": f"{team1} vs {team2}",
                            "team1": team1,
                            "team2": team2,
                            "outcomes": ["1", "X", "2"],
                            "odds": odds,
                            "container_index": i,
                            "start_time": start_time,
                            "page_num": current_page,
                            "url": match_url  # Store the match URL
                        }
                        matches.append(match)
                        time_str = f" (starts: {start_time})" if start_time else ""
                        url_str = f" [URL cached]" if match_url else ""
                        print(f"  Match {len(matches)}: {match['name']} - Odds: {match['odds']}{time_str}{url_str}")
                        
                except Exception as e:
                    print(f"  Error parsing match {i+1}: {e}")
                    continue
            
            # If we have enough matches, stop
            if len(matches) >= num_matches:
                break
            
            # Try to click Next button to go to next page
            if current_page < max_pages:
                try:
                    print(f"  Clicking 'Next' to load page {current_page + 1}...")
                    next_button_selectors = [
                        'button[aria-label="Next"]',
                        'button.p-button:has-text("Next")',
                        'button:has-text("Next")',
                    ]
                    
                    next_button = None
                    for selector in next_button_selectors:
                        try:
                            btn = await page.wait_for_selector(selector, timeout=2000, state='visible')
                            if btn and await btn.is_enabled():
                                next_button = btn
                                break
                        except:
                            continue
                    
                    if next_button:
                        await next_button.click()
                        await page.wait_for_timeout(1000)
                        await page.evaluate('window.scrollTo(0, 0)')
                        await page.wait_for_timeout(500)
                        current_page += 1
                    else:
                        print(f"  No 'Next' button found - reached end at page {current_page}")
                        break
                except Exception as e:
                    print(f"  Could not navigate to next page: {e}")
                    break
            else:
                break
        
        print(f"\nSuccessfully scraped {len(matches)} matches across {current_page} page(s)")
        
    except Exception as e:
        print(f"Error scraping matches: {e}")
    
    return matches

def generate_bet_combinations(matches, num_matches):
    """Generate all bet combinations for the given matches"""
    print(f"\nGenerating bet combinations for {num_matches} matches...")
    
    if len(matches) < num_matches:
        print(f"[WARNING] Only {len(matches)} matches available, need {num_matches}")
        return []
    
    selected_matches = matches[:num_matches]
    
    outcomes_per_match = []
    for match in selected_matches:
        outcomes_per_match.append(match.get("outcomes", ["1", "X", "2"]))
    
    all_combinations = list(product(*outcomes_per_match))
    
    print(f"\nGenerating combinations using DIFFERENT selections for the SAME {num_matches} matches:")
    for i, match in enumerate(selected_matches, 1):
        print(f"  Match {i}: {match['name']}")
    
    bet_slips = []
    
    for i, combination in enumerate(all_combinations, 1):
        slip = {
            "slip_number": i,
            "matches": selected_matches,
            "selections": combination,
            "total_combinations": len(all_combinations)
        }
        bet_slips.append(slip)
    
    print(f"\n✅ Generated {len(bet_slips)} VALID tickets")
    
    # Show dynamic examples based on number of matches
    if num_matches == 1:
        print(f"\nEach ticket bets on the SAME match with a DIFFERENT outcome prediction:")
        print(f"  Ticket 1 = Match1:1 (Home Win)")
        print(f"  Ticket 2 = Match1:X (Draw)")
        print(f"  Ticket 3 = Match1:2 (Away Win)")
        print(f"  This is a SINGLE BET - one of these will always win!")
    elif num_matches == 2:
        print(f"\nEach ticket bets on ALL {num_matches} matches with DIFFERENT outcome predictions:")
        print(f"  Ticket 1 = Match1:1, Match2:1")
        print(f"  Ticket 2 = Match1:1, Match2:X")
        print(f"  Ticket 3 = Match1:1, Match2:2")
        print(f"  ... (9 total combinations)")
        print(f"  This is a MULTI-BET (accumulator) where ALL selections must win.")
    else:
        print(f"\nEach ticket bets on ALL {num_matches} matches with DIFFERENT outcome predictions:")
        examples = [f"Match{i+1}:1" for i in range(min(num_matches, 3))]
        if num_matches > 3:
            examples.append("...")
        print(f"  Ticket 1 = {', '.join(examples)}")
        print(f"  ... ({len(bet_slips)} total combinations)")
        print(f"  This is a MULTI-BET (accumulator) where ALL selections must win.")
    
    return bet_slips

async def place_bet_slip(page: Page, bet_slip: dict, amount: float, match_cache: dict = None, outcome_button_cache: dict = None):
    """Place a single bet slip
    
    Args:
        page: Playwright page object
        bet_slip: Dictionary containing bet slip information
        amount: Amount to bet
        match_cache: Optional dictionary containing cached match positions to avoid re-searching
        outcome_button_cache: Optional dictionary containing cached outcome buttons keyed by match URL
    """
    slip_num = bet_slip["slip_number"]
    total = bet_slip["total_combinations"]
    matches = bet_slip["matches"]
    selections = bet_slip["selections"]
    
    print(f"\nPlacing bet slip {slip_num}/{total}...")
    print(f"Selections: {selections}")
    print(f"Amount: R {amount:.2f}")
    
    try:
        # Ensure we're on a valid page before reloading
        current_url = page.url
        print(f"  Current URL: {current_url}")
        
        # If we're on a match detail page, navigate to matches list first
        if '/event/' in current_url:
            print("  Currently on match detail page - navigating to matches list...")
            try:
                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(1000)
            except Exception as nav_error:
                print(f"  ⚠️ Navigation failed: {nav_error}")
                return False
        
        # Clear betslip using the "Remove All" button (more reliable than navigation)
        print("  Clearing betslip...")
        betslip_cleared = False
        
        # First, check if betslip has any selections
        try:
            betslip_check = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
            if betslip_check:
                betslip_text = await betslip_check.inner_text()
                # Check for actual bet selections
                has_odds_value = bool(re.search(r'\d+\.\d{2}', betslip_text))
                has_return_value = 'Total Betway Return' in betslip_text or 'Return' in betslip_text
                has_bet_content = '1X2' in betslip_text or has_return_value
                
                if has_odds_value and has_bet_content:
                    print("    ⚠️ Betslip has existing selections - clearing...")
                    
                    # Method 1: Use the "Remove All" button (most reliable)
                    remove_all_btn = await page.query_selector('div#betslip-remove-all')
                    if remove_all_btn and await remove_all_btn.is_visible():
                        await remove_all_btn.click()
                        await page.wait_for_timeout(800)
                        print("    ✅ Clicked 'Remove All' button")
                        betslip_cleared = True
                    else:
                        # Method 2: Try to find and click individual remove buttons
                        remove_btns = await page.query_selector_all('svg[id="betslip-remove-all"], div#betslip-remove-all svg')
                        for btn in remove_btns:
                            try:
                                if await btn.is_visible():
                                    await btn.click()
                                    await page.wait_for_timeout(500)
                                    print("    ✅ Clicked remove button via SVG")
                                    betslip_cleared = True
                                    break
                            except:
                                continue
                        
                        if not betslip_cleared:
                            # Method 3: Navigate to clear (fallback)
                            print("    ⚠️ Remove button not found - using navigation fallback...")
                            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=20000)
                            await page.wait_for_timeout(1500)
                else:
                    print("    [OK] Betslip is empty - ready to add selections")
                    betslip_cleared = True
            else:
                print("    [OK] No betslip container found - proceeding")
                betslip_cleared = True
        except Exception as e:
            print(f"    ⚠️ Error checking betslip: {e}")
        
        # Verify betslip is now empty
        if betslip_cleared:
            await page.wait_for_timeout(500)
            try:
                betslip_recheck = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
                if betslip_recheck:
                    recheck_text = await betslip_recheck.inner_text()
                    still_has_bets = bool(re.search(r'\d+\.\d{2}', recheck_text)) and '1X2' in recheck_text
                    if still_has_bets:
                        print("    ⚠️ Betslip still has selections after clear attempt!")
                        # Try remove all one more time
                        remove_all_btn = await page.query_selector('div#betslip-remove-all')
                        if remove_all_btn:
                            await remove_all_btn.click()
                            await page.wait_for_timeout(800)
                            print("    ✅ Second attempt - clicked 'Remove All'")
            except:
                pass
        
        # Close any modals that may have appeared
        try:
            await close_all_modals(page, max_attempts=2)
        except:
            pass
        
        # Click on each match outcome
        for i, (match, selection) in enumerate(zip(matches, selections)):
            start_time = match.get('start_time', 'Unknown time')
            print(f"  Match {i+1}: {match['name']} | ⏰ {start_time} - Selecting: {selection}")
            
            expected_time = match.get('start_time')
            expected_team1 = match.get('team1')
            expected_team2 = match.get('team2')
            match_url = match.get('url')  # Get cached URL from initial scraping
            match_key = f"{expected_team1}_{expected_team2}_{expected_time}"
            
            selection_index = {"1": 0, "X": 1, "2": 2}.get(selection, 0)
            
            try:
                # Use cached URL from scraping (fastest and most reliable)
                if match_url:
                    print(f"    Using cached URL: {match_url}")
                    
                    # Navigate to match page to click buttons
                    await page.goto(match_url, wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(1500)
                    await close_all_modals(page)
                    await page.wait_for_timeout(1500)  # Increased wait for page to fully load
                    
                    # Check if we have cached selector for this match URL
                    outcome_buttons = []
                    if outcome_button_cache and match_url in outcome_button_cache:
                        # Use cached selector to query FRESH elements
                        cached_selector = outcome_button_cache[match_url]
                        print(f"    [CACHE HIT] Using cached selector: {cached_selector[:50]}...")
                        try:
                            # Wait for elements to be present before querying
                            await page.wait_for_timeout(1000)  # Additional wait for dynamic content
                            outcome_buttons = await page.query_selector_all(cached_selector)
                            if len(outcome_buttons) >= 3:
                                print(f"    ✓ Found {len(outcome_buttons)} fresh buttons using cached selector")
                            else:
                                print(f"    ⚠️  Cached selector returned {len(outcome_buttons)} buttons, trying fallback selectors...")
                                outcome_buttons = []
                        except Exception as e:
                            print(f"    ⚠️  Cached selector failed: {e}, trying fallback selectors...")
                            outcome_buttons = []
                    
                    # If cache miss or cached selector failed, try all selectors
                    if len(outcome_buttons) < 3:
                        button_selectors = [
                            'div.grid.p-1 > div.flex.items-center.justify-between.h-12',
                            'div[class*="grid"] > div[class*="flex items-center justify-between h-12"]',
                            'details:has(span:text("1X2")) div.grid > div',
                            'div[price]',
                            'button[data-translate-market-name="Full Time Result"] div[price]',
                            'div[data-translate-market-name="Full Time Result"] div[price]',
                        ]
                        
                        for selector in button_selectors:
                            try:
                                buttons = await page.query_selector_all(selector)
                                if len(buttons) >= 3:
                                    outcome_buttons = buttons
                                    print(f"    Found {len(buttons)} outcome buttons using selector: {selector}")
                                    # Cache the working selector
                                    if outcome_button_cache is not None:
                                        outcome_button_cache[match_url] = selector
                                        print(f"    [CACHE STORED] Selector cached for reuse")
                                    break
                            except:
                                continue
                    
                    if len(outcome_buttons) >= 3 and selection_index < len(outcome_buttons):
                        outcome_btn = outcome_buttons[selection_index]
                        selection_confirmed = False
                        max_click_attempts = 3
                        
                        for click_attempt in range(max_click_attempts):
                            try:
                                # Re-query buttons on retry to ensure fresh DOM elements
                                if click_attempt > 0:
                                    print(f"    🔄 Retry {click_attempt + 1}/{max_click_attempts}: Re-querying buttons...")
                                    await page.wait_for_timeout(1000)
                                    
                                    # Try to find buttons again with cached selector
                                    if outcome_button_cache and match_url in outcome_button_cache:
                                        cached_selector = outcome_button_cache[match_url]
                                        fresh_buttons = await page.query_selector_all(cached_selector)
                                        if len(fresh_buttons) >= 3:
                                            outcome_btn = fresh_buttons[selection_index]
                                        else:
                                            # Try fallback selectors
                                            for sel in ['div[price]', 'div.grid.p-1 > div.flex.items-center']:
                                                fresh_buttons = await page.query_selector_all(sel)
                                                if len(fresh_buttons) >= 3:
                                                    outcome_btn = fresh_buttons[selection_index]
                                                    break
                                
                                await outcome_btn.scroll_into_view_if_needed()
                                await page.wait_for_timeout(300)
                                
                                # Try multiple click methods
                                try:
                                    await outcome_btn.click()
                                except:
                                    try:
                                        await outcome_btn.evaluate('el => el.click()')
                                    except:
                                        await outcome_btn.dispatch_event('click')
                                
                                await page.wait_for_timeout(1500)
                                cache_status = "[CACHED]" if (outcome_button_cache and match_url in outcome_button_cache) else ""
                                print(f"    ✓ Clicked outcome '{selection}' {cache_status}")
                                
                                # VERIFY: Check if selection was added to betslip
                                await page.wait_for_timeout(500)
                                betslip_check = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
                                if betslip_check:
                                    betslip_text = await betslip_check.inner_text()
                                    # Look for indicators that a bet was added (not just header)
                                    has_selection = ('Single' in betslip_text and '1' in betslip_text) or \
                                                   ('Multi' in betslip_text and '1' in betslip_text) or \
                                                   '1X2' in betslip_text or \
                                                   'Return' in betslip_text
                                    
                                    # Also check betslip isn't just showing the empty header
                                    betslip_has_content = len(betslip_text) > 100  # Empty betslip is very short
                                    
                                    if has_selection and betslip_has_content:
                                        print(f"    ✓ Selection confirmed in betslip")
                                        selection_confirmed = True
                                        break
                                    else:
                                        print(f"    ⚠️ Selection not detected in betslip (attempt {click_attempt + 1}/{max_click_attempts})")
                                        if click_attempt < max_click_attempts - 1:
                                            # Try scrolling to refresh the view
                                            await page.evaluate('window.scrollTo(0, 0)')
                                            await page.wait_for_timeout(300)
                                            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                                else:
                                    print(f"    ⚠️ Could not find betslip container (attempt {click_attempt + 1}/{max_click_attempts})")
                                    
                            except Exception as click_err:
                                print(f"    ⚠️ Click attempt {click_attempt + 1} failed: {click_err}")
                                if click_attempt == max_click_attempts - 1:
                                    print(f"    ❌ ERROR clicking button after {max_click_attempts} attempts")
                                    return False
                        
                        if not selection_confirmed:
                            print(f"    ❌ ERROR: Could not confirm selection was added to betslip after {max_click_attempts} attempts")
                            return False
                            
                    else:
                        print(f"    ❌ ERROR: Could not find outcome buttons on match page (found {len(outcome_buttons)} buttons)")
                        print(f"    Page URL: {page.url}")
                        # Try to get page content for debugging
                        try:
                            page_text = await page.query_selector('body')
                            if page_text:
                                text = await page_text.inner_text()
                                if 'Full Time Result' in text or '1X2' in text:
                                    print(f"    Found market text but couldn't locate buttons")
                        except:
                            pass
                        return False
                else:
                    print(f"    ❌ ERROR: No cached URL available for match")
                    return False
                
            except Exception as e:
                print(f"    [ERROR] Failed to click outcome: {e}")
                return False
        
        # Wait for betslip to fully update with all selections
        await page.wait_for_timeout(1000)
        
        # CRITICAL: Scroll to make betslip visible and wait for it to load
        print("  Scrolling to betslip...")
        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
        await page.wait_for_timeout(1000)
        
        # Try to click betslip toggle button if it exists (mobile view may have collapsed betslip)
        try:
            betslip_toggle_selectors = [
                'button[aria-label*="Betslip"]',
                'div#betslip-toggle',
                'button:has-text("Betslip")',
                'div.betslip-toggle',
            ]
            for toggle_sel in betslip_toggle_selectors:
                try:
                    toggle_btn = await page.query_selector(toggle_sel)
                    if toggle_btn and await toggle_btn.is_visible():
                        await toggle_btn.click()
                        print(f"    Clicked betslip toggle: {toggle_sel}")
                        await page.wait_for_timeout(1000)
                        break
                except:
                    continue
        except:
            pass
        
        # Enter bet amount
        print(f"  Entering bet amount: R {amount:.2f}")
        try:
            # Try multiple selectors with retries (betslip may take time to appear)
            stake_input = None
            stake_selectors = [
                '#bet-amount-input',
                'input[placeholder="0.00"]',
                'input[type="number"][inputmode="decimal"]',
                'div#betslip-container-mobile input[type="number"]',
                'div#betslip-container input[type="number"]',
                'input[id*="bet-amount"]',
                'input[class*="stake"]',
                'input[placeholder*="0"]',  # Any placeholder with 0
            ]
            
            # Try to find stake input with retries (up to 5 attempts, 1 second apart)
            for find_attempt in range(5):
                for selector in stake_selectors:
                    try:
                        stake_input = await page.query_selector(selector)
                        if stake_input and await stake_input.is_visible():
                            print(f"    Found stake input using: {selector}")
                            break
                    except:
                        continue
                
                if stake_input:
                    break
                
                # If not found, try additional recovery steps
                if find_attempt < 4:
                    print(f"    ⏳ Stake input not found, retrying ({find_attempt + 1}/5)...")
                    
                    # Try scrolling in different ways
                    if find_attempt == 0:
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    elif find_attempt == 1:
                        # Try clicking on the betslip header/panel to expand it
                        try:
                            betslip_header_selectors = [
                                'span:has-text("Betslip")',
                                'div#betslip-container-mobile',
                                'div#betslip-container',
                                'div[class*="betslip"] span',
                            ]
                            for header_sel in betslip_header_selectors:
                                try:
                                    header = await page.query_selector(header_sel)
                                    if header and await header.is_visible():
                                        await header.click()
                                        print(f"    Clicked betslip header to expand")
                                        await page.wait_for_timeout(500)
                                        break
                                except:
                                    continue
                        except:
                            pass
                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    elif find_attempt == 2:
                        # Try clicking on the Single tab in betslip (for single bets)
                        try:
                            single_tab = await page.query_selector('button:has-text("Single")')
                            if single_tab and await single_tab.is_visible():
                                await single_tab.click()
                                print(f"    Clicked 'Single' tab to show stake input")
                                await page.wait_for_timeout(500)
                        except:
                            pass
                        # Also try Multi tab
                        try:
                            multi_tab = await page.query_selector('div#betslip-container-mobile button:has-text("Multi")')
                            if not multi_tab:
                                multi_tab = await page.query_selector('button:has-text("Multi")')
                            if multi_tab and await multi_tab.is_visible():
                                await multi_tab.click()
                                print(f"    Clicked 'Multi' tab to show stake input")
                        except:
                            pass
                    elif find_attempt == 3:
                        # Debug: Print what's in the betslip container
                        try:
                            betslip = await page.query_selector('div#betslip-container-mobile')
                            if not betslip:
                                betslip = await page.query_selector('div#betslip-container')
                            if betslip:
                                betslip_html = await betslip.inner_html()
                                print(f"    🔍 Debug: Betslip HTML (first 500 chars): {betslip_html[:500]}")
                            else:
                                print(f"    🔍 Debug: No betslip container found!")
                                
                            # Also list all input elements on page
                            all_inputs = await page.query_selector_all('input')
                            print(f"    🔍 Debug: Found {len(all_inputs)} input elements on page")
                            for idx, inp in enumerate(all_inputs[:5]):
                                try:
                                    inp_id = await inp.get_attribute('id') or 'no-id'
                                    inp_type = await inp.get_attribute('type') or 'no-type'
                                    inp_placeholder = await inp.get_attribute('placeholder') or 'no-placeholder'
                                    inp_visible = await inp.is_visible()
                                    print(f"      Input {idx+1}: id={inp_id}, type={inp_type}, placeholder={inp_placeholder}, visible={inp_visible}")
                                except:
                                    pass
                        except Exception as debug_err:
                            print(f"    🔍 Debug error: {debug_err}")
                    
                    await page.wait_for_timeout(1000)
            
            if stake_input:
                print("    Found stake input, setting value...")
                amount_str = str(amount)
                amount_entered = False
                
                # Try multiple methods to enter amount with verification
                for attempt in range(3):
                    try:
                        # Use JavaScript directly (fill() doesn't work with number inputs that have validation)
                        await stake_input.evaluate('''(el, amount) => {
                            // Clear and set value
                            el.value = '';
                            el.focus();
                            el.value = amount;
                            
                            // Trigger all necessary events for React/form validation
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.blur();
                        }''', amount_str)
                        
                        await page.wait_for_timeout(400)
                        
                        # VERIFY: Check if value was actually set
                        entered_value = await stake_input.input_value()
                        print(f"    Attempt {attempt + 1}: Input field value = '{entered_value}'")
                        
                        if entered_value and str(entered_value).strip() == amount_str:
                            print("    ✓ Amount successfully entered and verified!")
                            amount_entered = True
                            break
                        elif attempt < 2:
                            print(f"    Retrying amount entry (attempt {attempt + 2}/3)...")
                            await page.wait_for_timeout(500)
                        
                    except Exception as js_error:
                        print(f"    Error on attempt {attempt + 1}: {js_error}")
                        if attempt < 2:
                            await page.wait_for_timeout(500)
                
                if not amount_entered:
                    print("    ❌ FAILED to enter amount after 3 attempts!")
                    return False
                    
                # CRITICAL: Wait for betslip to fully update after amount entry
                print("    Waiting for betslip to update...")
                await page.wait_for_timeout(1500)
                
            else:
                print("    ! Could not find stake input field")
                return False
        except Exception as e:
            print(f"    [ERROR] Failed to enter amount: {e}")
            return False
        
        # Check for betslip errors or conflicts before placing
        print("  Checking for conflicts or errors...")
        
        await page.wait_for_timeout(800)
        
        # Close any modals that might have appeared after amount entry (aggressive check)
        await close_all_modals(page, max_attempts=2)
        
        # CRITICAL: Verify betslip is ready before clicking Bet Now
        print("  Verifying betslip is ready to place...")
        betslip_ready = False
        max_retries = 5
        
        for retry in range(max_retries):
            # Scroll down to ensure betslip is visible
            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await page.wait_for_timeout(500)
            
            # Get betslip content for validation
            betslip_container = await page.query_selector('div#betslip-container-mobile')
            if not betslip_container:
                betslip_container = await page.query_selector('div#betslip-container')
            if not betslip_container:
                betslip_container = await page.query_selector('div[class*="betslip"]')
            
            if betslip_container:
                betslip_text = await betslip_container.inner_text()
                
                # More flexible checks - look for any indication betslip is ready
                has_bet_button = 'Bet Now' in betslip_text or 'bet now' in betslip_text.lower() or 'Place Bet' in betslip_text
                has_return_calculation = 'Return' in betslip_text or 'Total' in betslip_text
                has_odds = any(char.isdigit() and '.' in betslip_text for char in betslip_text)
                
                # Check for stake amount - look for the actual stake value OR verify return is calculated
                stake_str = str(amount)
                # Also check for amount without decimal if it's a whole number
                stake_int_str = str(int(amount)) if amount == int(amount) else None
                
                # Betslip shows stake in different ways - check for any of them
                has_stake = (
                    stake_str in betslip_text or 
                    f"R {stake_str}" in betslip_text or 
                    f"R{stake_str}" in betslip_text or
                    (stake_int_str and f"R {stake_int_str}" in betslip_text) or
                    (stake_int_str and f"R{stake_int_str}" in betslip_text)
                )
                
                # ALTERNATIVE: If return is shown with a valid amount, stake was entered
                # Check if there's a return value that makes sense (return = stake * odds)
                has_valid_return = False
                if has_return_calculation:
                    # Look for "Return:R X.XX" pattern - if present, stake was entered
                    return_match = re.search(r'Return[:\s]*R\s*(\d+\.?\d*)', betslip_text)
                    if return_match:
                        return_value = float(return_match.group(1))
                        # If return is greater than 0, stake was entered
                        if return_value > 0:
                            has_valid_return = True
                
                # Betslip is ready if: has bet button AND (stake visible OR valid return calculated)
                if has_bet_button and (has_stake or has_valid_return):
                    betslip_ready = True
                    print(f"    ✓ Betslip is READY (retry {retry + 1}/{max_retries})")
                    break
                else:
                    # Check if session expired (Login button appears instead of Bet Now)
                    # Look for "Login" right before "share" which indicates the button position
                    is_logged_out = ('Login' in betslip_text and 'Bet Now' not in betslip_text) or \
                                   ('Loginshare' in betslip_text.replace(' ', ''))
                    
                    if is_logged_out:
                        print(f"    ⚠️ [SESSION EXPIRED] Detected 'Login' instead of 'Bet Now' - attempting re-login...")
                        return "RELOGIN"  # Signal that re-login is needed
                    
                    # Show which validation failed
                    missing = []
                    if not has_bet_button:
                        missing.append("Bet Button")
                    if not has_return_calculation:
                        missing.append("Return")
                    if not has_stake:
                        missing.append(f"Stake ({stake_str})")
                    print(f"    ⏳ Missing: {', '.join(missing)} (retry {retry + 1}/{max_retries})")
                    
                    # Debug: print first 200 chars of betslip content on last retry
                    if retry == max_retries - 1:
                        print(f"    🔍 Debug betslip content: {betslip_text[:200]}")
                
                if retry < max_retries - 1:
                    await page.wait_for_timeout(1000)
            else:
                print(f"    ⚠️ Could not find betslip container (retry {retry + 1}/{max_retries})")
                if retry < max_retries - 1:
                    await page.wait_for_timeout(1000)
        
        if not betslip_ready:
            print(f"    ❌ [ERROR] Betslip not ready after {max_retries} retries - stake amount not visible!")
            print(f"    This usually means the amount wasn't entered correctly")
            return "RETRY"  # Return RETRY instead of False to trigger automatic retry
        
        # Get betslip content for debugging (try mobile container first from HTML)
        betslip_container = await page.query_selector('div#betslip-container-mobile')
        if not betslip_container:
            betslip_container = await page.query_selector('div#betslip-container')
        if not betslip_container:
            betslip_container = await page.query_selector('div[class*="betslip"]')
        if betslip_container:
            betslip_text = await betslip_container.inner_text()
            print(f"    Betslip contents: {betslip_text[:300]}")
            
            # CRITICAL: Check if user is logged out (betslip shows Login text but no betting elements)
            # The word "Login" appears in T&Cs, so check for absence of key betting indicators
            has_betting_elements = any(indicator in betslip_text for indicator in ['Total', 'Return', 'Stake', '1X2'])
            if 'Login' in betslip_text and not has_betting_elements:
                print(f"\n    ❌❌❌ [CRITICAL ERROR] USER IS LOGGED OUT! ❌❌❌")
                print(f"    ❌ Session expired - need to re-login")
                print(f"    ❌ This usually happens after browser restart or long session")
                print(f"    ❌ ABORTING BET - restart script to re-authenticate\n")
                return False
            
            # CRITICAL: Check for conflict message (case insensitive)
            betslip_lower = betslip_text.lower()
            has_conflict = ('conflicting' in betslip_lower and 'selection' in betslip_lower) or \
                          'conflict' in betslip_lower or \
                          ('there are' in betslip_lower and 'revise' in betslip_lower)
            
            if has_conflict:
                print(f"\n    ❌❌❌ [CRITICAL ERROR] CONFLICTING SELECTIONS DETECTED! ❌❌❌")
                print(f"    ❌ Betslip was NOT properly cleared - old selections remain")
                print(f"    ❌ ABORTING BET IMMEDIATELY\n")
                
                return False
            
            # Check if betslip has correct number of selections
            selection_count = betslip_text.count('1X2')
            expected_count = len(matches)
            
            if selection_count != expected_count:
                print(f"\n    ❌ [ERROR] Wrong number of selections in betslip!")
                print(f"    Expected: {expected_count} selection(s)")
                print(f"    Found: {selection_count} selection(s)")
                
                if selection_count > expected_count:
                    print(f"    ❌ Too many selections - betslip not properly cleared")
                elif selection_count < expected_count:
                    print(f"    ❌ Missing selections - not all matches were added")
                
                print(f"    ❌ ABORTING - will retry with cleared betslip\n")
                
                # Try to clear the betslip before returning
                try:
                    remove_all_btn = await page.query_selector('div#betslip-remove-all')
                    if remove_all_btn and await remove_all_btn.is_visible():
                        await remove_all_btn.click()
                        await page.wait_for_timeout(500)
                        print(f"    ✓ Cleared betslip for retry")
                except:
                    pass
                
                return "RETRY"  # Signal retry instead of hard failure
            
            # Check for error messages in betslip
            error_selectors = [
                'div.error-message',
                'div[class*="error"]',
                'span[class*="error"]',
                'div.betslip-error',
                'div[class*="conflict"]',
                'span[class*="conflict"]',
                'div[role="alert"]'
            ]
            
            for selector in error_selectors:
                error_elements = await page.query_selector_all(selector)
                for error_elem in error_elements:
                    error_text = await error_elem.inner_text()
                    if error_text and len(error_text.strip()) > 0:
                        print(f"    [ERROR] Betslip error detected: {error_text}")
                        if 'conflict' in error_text.lower() or 'related' in error_text.lower():
                            print("    [ERROR] Conflicting selections - aborting this bet")
                            return False
        
        # ===== PRE-BET VERIFICATION =====
        # 1. Capture balance BEFORE placing bet
        print("  [PRE-BET] Capturing current balance...")
        balance_before = await get_current_balance(page)
        if balance_before > 0:
            print(f"    ✓ Balance before bet: R{balance_before:.2f}")
        else:
            print(f"    ⚠️ Could not capture balance - will proceed without balance verification")
        
        # 2. Verify correct number of selections
        expected_selections = len(matches)
        if not await verify_selections_before_bet(page, expected_selections):
            print(f"    ❌ [PRE-BET] Selection count mismatch - aborting bet!")
            return False
        
        # Click place bet button
        print("  Attempting to place bet...")
        
        await close_all_modals(page, max_attempts=2)
        await page.wait_for_timeout(500)
        
        try:
            # CRITICAL: Container-scoped selectors to find "Bet Now" button
            # Prioritizes searching within betslip containers (div#betslip-container, div#betslip-container-mobile)
            # to avoid clicking unrelated buttons elsewhere on the page
            bet_button_selectors = [
                # First priority: Look inside betslip container with exact ID from HTML
                'div#betslip-container button#betslip-strike-btn',
                'button#betslip-strike-btn',
                '#betslip-strike-btn',
                
                # Secondary: Look by aria-label INSIDE betslip
                'div#betslip-container button[aria-label="Bet Now"]',
                'div#betslip-container-mobile button[aria-label="Bet Now"]',
                'button[aria-label="Bet Now"]:not(#sign-up-btn)',
                
                # Tertiary: Look by text content INSIDE betslip container
                'div#betslip-container button:has-text("Bet Now")',
                'div#betslip-container-mobile button:has-text("Bet Now")',
                'button.p-button:has-text("Bet Now"):not(#sign-up-btn)',
                
                # Quaternary: Look by class combinations from HTML BUT exclude sign-up button
                'div#betslip-container button.p-button.bg-identity',
                'div#betslip-container-mobile button.p-button.bg-identity',
                'button.p-button.bg-identity:not(#sign-up-btn)',
                
                # Last resort: Any button with Bet in betslip container (explicitly exclude sign-up)
                'div#betslip-container button:has-text("Bet"):not(#sign-up-btn)',
                'div#betslip-container-mobile button:has-text("Bet"):not(#sign-up-btn)',
            ]
            
            place_bet_btn = None
            successful_selector = None
            
            for selector in bet_button_selectors:
                try:
                    btn = await page.wait_for_selector(selector, timeout=2000, state='visible')
                    if btn:
                        is_visible = await btn.is_visible()
                        is_enabled = await btn.is_enabled()
                        
                        # CRITICAL: Verify this is NOT the sign-up button
                        btn_id = await btn.get_attribute('id')
                        if btn_id == 'sign-up-btn':
                            print(f"    ⚠️  Skipping sign-up button (selector: {selector})")
                            continue
                        
                        # CRITICAL: Get button text and verify it's not account-related
                        try:
                            btn_text = (await btn.inner_text()).lower()
                            skip_keywords = ['account', 'deposit', 'withdraw', 'profile', 'login', 'sign', 'register']
                            if any(kw in btn_text for kw in skip_keywords):
                                print(f"    ⚠️  Skipping account-related button: '{btn_text}' (selector: {selector})")
                                continue
                        except:
                            pass
                        
                        if is_visible and is_enabled:
                            place_bet_btn = btn
                            successful_selector = selector
                            print(f"    ✓ Found enabled bet button: {selector}")
                            break
                        elif is_visible and not is_enabled:
                            print(f"    ⚠️  Button found but disabled: {selector}")
                        else:
                            print(f"    ⚠️  Button found but not visible: {selector}")
                except Exception as e:
                    continue
            
            if not place_bet_btn:
                print("    ❌ [ERROR] Could not find enabled Bet Now button!")
                print("    Attempting to capture all buttons for debugging...")
                
                # Try to list all buttons for debugging
                try:
                    all_buttons = await page.query_selector_all('button')
                    print(f"    Found {len(all_buttons)} buttons total:")
                    for i, btn in enumerate(all_buttons[:15]):  # Show first 15
                        try:
                            text = await btn.inner_text()
                            classes = await btn.get_attribute('class')
                            btn_id = await btn.get_attribute('id')
                            is_vis = await btn.is_visible()
                            print(f"      {i+1}. text='{text[:30]}' id='{btn_id}' visible={is_vis}")
                        except:
                            pass
                except:
                    pass
                
                return False
            
            # Double-check button is not disabled
            is_disabled = await place_bet_btn.get_attribute('disabled')
            aria_disabled = await place_bet_btn.get_attribute('aria-disabled')
            
            if is_disabled or aria_disabled == 'true':
                print(f"    ❌ [ERROR] Bet Now button is DISABLED!")
                print(f"       disabled attribute: {is_disabled}")
                print(f"       aria-disabled: {aria_disabled}")
                return False
            
            print(f"    Button ready to click (selector: {successful_selector})")
            
            # Try multiple click methods to handle DOM detachment and ensure modal appears
            # Re-queries button before each attempt to avoid "element detached from DOM" errors
            # Stops after first successful method (when confirmation modal is detected)
            print("    Attempting to click Bet Now button...")
            click_success = False
            modal_appeared = False
            
            # Helper function to check if confirmation modal appeared
            async def check_for_modal():
                # FIRST: Check if this is an Account Options modal (NOT a bet confirmation)
                # Account modal has unique identifiers we should exclude
                account_modal_indicators = [
                    '#deposit-account-nav',
                    '#withdraw-account-nav', 
                    '#banking-iframe-deposit',
                    'text="Account Options"',
                    'text="Deposit funds"',
                    '[aria-label="Deposit funds"]',
                ]
                
                for indicator in account_modal_indicators:
                    try:
                        elem = await page.query_selector(indicator)
                        if elem and await elem.is_visible():
                            print(f"    ⚠️ [check_for_modal] Detected Account modal (not bet confirmation) - indicator: {indicator}")
                            return False  # This is NOT a bet confirmation modal
                    except:
                        continue
                
                # Check for SPECIFIC bet confirmation elements (not generic modal selectors)
                # These are unique to the bet confirmation modal
                confirmation_selectors = [
                    'button#strike-conf-continue-btn',  # "Continue betting" button - most specific
                    'span:has-text("Bet Confirmation")',  # Title of confirmation modal
                    'button:has-text("Continue betting")',  # Text of continue button
                    'div:has-text("Your bet has been placed")',  # Success message
                ]
                
                for selector in confirmation_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            print(f"    ✓ [check_for_modal] Found bet confirmation element: {selector}")
                            return True
                    except:
                        pass
                
                # Fallback: check generic modal but verify it's not Account modal
                generic_modal_selectors = [
                    'button:has-text("Confirm")',
                    'button:has-text("Place Bet")',
                    'button:has-text("OK")',
                ]
                for selector in generic_modal_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            return True
                    except:
                        pass
                return False
            
            # Helper function to check for and close Account Options modal
            async def check_and_close_account_modal():
                """Detects and closes the Account Options / Deposit funds modal that sometimes appears"""
                # Use more specific selectors based on the actual modal HTML
                account_indicators = [
                    '#deposit-account-nav',  # Most reliable - the deposit nav tab
                    '#withdraw-account-nav',  # Withdraw nav tab
                    '#banking-iframe-deposit',  # Banking iframe
                    '[aria-label="Deposit funds"]',  # Aria label
                    'text="Account Options"',
                    'text="Deposit funds"',
                ]
                
                detected = False
                for indicator in account_indicators:
                    try:
                        elem = await page.query_selector(indicator)
                        if elem and await elem.is_visible():
                            detected = True
                            print(f"    ⚠️ [ACCOUNT MODAL] Detected via: {indicator}")
                            break
                    except:
                        continue
                
                if not detected:
                    return False
                    
                print(f"    ⚠️ [ACCOUNT MODAL] Closing Account Options/Deposit modal...")
                
                # Try pressing Escape first
                await page.keyboard.press('Escape')
                await page.wait_for_timeout(500)
                
                # Check if it's still there using the most reliable indicator
                still_visible = False
                for indicator in account_indicators[:3]:  # Check the reliable selectors
                    try:
                        elem = await page.query_selector(indicator)
                        if elem and await elem.is_visible():
                            still_visible = True
                            break
                    except:
                        continue
                
                if still_visible:
                    # Try clicking close button
                    close_selectors = [
                        'svg[id="modal-close-btn"]',
                        '#modal-close-btn',
                        'button[aria-label="Close"]',
                        'button:has-text("×")',
                        '.modal-close-btn',
                    ]
                    for close_sel in close_selectors:
                        try:
                            close_btns = await page.query_selector_all(close_sel)
                            for close_btn in close_btns:
                                if await close_btn.is_visible():
                                    await close_btn.click()
                                    await page.wait_for_timeout(300)
                                    print(f"    ✓ Clicked close button: {close_sel}")
                                    break
                        except:
                            continue
                    
                    # Press Escape again as backup
                    await page.keyboard.press('Escape')
                    await page.wait_for_timeout(300)
                
                print(f"    ✓ Account modal closed")
                return True
            
            # Helper function to re-query button (Betway re-renders the betslip)
            async def get_fresh_button():
                # Wait a bit for DOM to stabilize after betslip update
                await page.wait_for_timeout(800)
                
                for selector in bet_button_selectors:
                    try:
                        btn = await page.wait_for_selector(selector, timeout=3000, state='attached')
                        if btn and await btn.is_visible() and await btn.is_enabled():
                            # Double-check it's still enabled (Betway may update it)
                            await page.wait_for_timeout(300)
                            if await btn.is_enabled():
                                return btn
                    except:
                        continue
                return None
            
            # Method 1: Scroll into view and JavaScript click
            if not modal_appeared:
                try:
                    fresh_btn = await get_fresh_button()
                    if fresh_btn:
                        # Ensure button is fully visible and stable
                        await fresh_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(500)
                        
                        # Verify button is still enabled before clicking
                        is_enabled = await fresh_btn.is_enabled()
                        if not is_enabled:
                            print("    ⚠️  Button became disabled - waiting...")
                            await page.wait_for_timeout(1000)
                            fresh_btn = await get_fresh_button()
                        
                        if fresh_btn:
                            await fresh_btn.evaluate('el => el.click()')
                            await page.wait_for_timeout(1500)  # Increased wait for modal
                    
                    # CRITICAL: Check if Account Options modal appeared instead of bet confirmation
                    account_modal_closed = await check_and_close_account_modal()
                    if account_modal_closed:
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal()
                    if modal_appeared:
                        print("    ✅ Method 1: JavaScript click SUCCESS - modal appeared!")
                        click_success = True
                    else:
                        print("    ✗ Method 1: Click executed but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 1 failed: {e}")
            
            # Method 2: Direct Playwright click
            if not modal_appeared:
                try:
                    fresh_btn = await get_fresh_button()
                    if fresh_btn:
                        await fresh_btn.click(timeout=3000, force=True)
                    await page.wait_for_timeout(1000)
                    
                    # Check for Account Options modal
                    account_modal_closed = await check_and_close_account_modal()
                    if account_modal_closed:
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal()
                    if modal_appeared:
                        print("    ✅ Method 2: Direct click SUCCESS - modal appeared!")
                        click_success = True
                    else:
                        print("    ✗ Method 2: Click executed but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 2 failed: {e}")
            
            # Method 3: Dispatch click event
            if not modal_appeared:
                try:
                    fresh_btn = await get_fresh_button()
                    if fresh_btn:
                        await fresh_btn.dispatch_event('click')
                    await page.wait_for_timeout(1000)
                    
                    # Check for Account Options modal
                    account_modal_closed = await check_and_close_account_modal()
                    if account_modal_closed:
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal()
                    if modal_appeared:
                        print("    ✅ Method 3: Dispatch click SUCCESS - modal appeared!")
                        click_success = True
                    else:
                        print("    ✗ Method 3: Click executed but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 3 failed: {e}")
            
            # Method 4: Focus and press Enter
            if not modal_appeared:
                try:
                    fresh_btn = await get_fresh_button()
                    if fresh_btn:
                        await fresh_btn.focus()
                        await page.wait_for_timeout(300)
                    await page.keyboard.press('Enter')
                    await page.wait_for_timeout(1000)
                    
                    # Check for Account Options modal
                    account_modal_closed = await check_and_close_account_modal()
                    if account_modal_closed:
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal()
                    if modal_appeared:
                        print("    ✅ Method 4: Enter key SUCCESS - modal appeared!")
                        click_success = True
                    else:
                        print("    ✗ Method 4: Enter pressed but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 4 failed: {e}")
            
            # Method 5: Mouse click at button coordinates
            if not modal_appeared:
                try:
                    fresh_btn = await get_fresh_button()
                    if fresh_btn:
                        box = await fresh_btn.bounding_box()
                        if box:
                            x = box['x'] + box['width'] / 2
                            y = box['y'] + box['height'] / 2
                            await page.mouse.click(x, y)
                        await page.wait_for_timeout(1000)
                        
                        # Check for Account Options modal
                        account_modal_closed = await check_and_close_account_modal()
                        if account_modal_closed:
                            print("    ⚠️ Account modal was triggered instead of bet - will retry")
                        
                        modal_appeared = await check_for_modal()
                        if modal_appeared:
                            print("    ✅ Method 5: Mouse click SUCCESS - modal appeared!")
                            click_success = True
                        else:
                            print("    ✗ Method 5: Mouse click but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 5 failed: {e}")
            
            # Method 6: Force page refresh and try again with fresh DOM
            if not modal_appeared:
                try:
                    print("    ⚠️ Trying Method 6: Page refresh and retry...")
                    # Reload the current match page to get fresh DOM
                    current_url = page.url
                    await page.reload(wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(2000)
                    await close_all_modals(page)
                    
                    # Re-enter the bet amount since page was reloaded
                    stake_input = await page.query_selector('#bet-amount-input')
                    if not stake_input:
                        stake_input = await page.query_selector('input[placeholder="0.00"]')
                    if stake_input:
                        await stake_input.evaluate('''(el, amt) => {
                            el.value = '';
                            el.focus();
                            el.value = amt;
                            el.dispatchEvent(new Event('input', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                            el.blur();
                        }''', str(amount))
                        await page.wait_for_timeout(1500)
                    
                    # Try to click the fresh button
                    fresh_btn = await get_fresh_button()
                    if fresh_btn:
                        await fresh_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(500)
                        await fresh_btn.click(timeout=3000, force=True)
                        await page.wait_for_timeout(1500)
                        modal_appeared = await check_for_modal()
                        if modal_appeared:
                            print("    ✅ Method 6: Reload + click SUCCESS - modal appeared!")
                            click_success = True
                        else:
                            print("    ✗ Method 6: Reload + click but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 6 failed: {e}")
            
            if not click_success or not modal_appeared:
                print("    ❌ [ERROR] All click methods failed to trigger modal!")
                # Try to get button's computed style and state for debugging
                try:
                    button_info = await place_bet_btn.evaluate('''el => {
                        const style = window.getComputedStyle(el);
                        return {
                            display: style.display,
                            visibility: style.visibility,
                            opacity: style.opacity,
                            pointerEvents: style.pointerEvents,
                            zIndex: style.zIndex,
                            position: style.position,
                            disabled: el.disabled,
                            ariaDisabled: el.getAttribute('aria-disabled'),
                            classList: Array.from(el.classList),
                            id: el.id,
                        };
                    }''')
                    print(f"    🔍 Button debug info: {button_info}")
                except:
                    pass
                # Return special code to indicate retry needed
                return "RETRY"
            
            print("    ✅ Bet Now button clicked and confirmation modal appeared!")
            
            # Modal already appeared, no need to wait again
            await page.wait_for_timeout(500)
            
            # CRITICAL: Check for PRICE CHANGE modal first and accept new odds
            # Price change modals appear when odds change between selection and placement
            price_change_handled = False
            price_change_selectors = [
                'button:has-text("Accept")',
                'button:has-text("Accept Changes")',
                'button:has-text("Accept New Odds")',
                'button:has-text("Accept Odds")',
                'button:has-text("Accept Price")',
                'button:has-text("Accept new price")',
                'button:has-text("Confirm")',
                'button[aria-label*="Accept"]',
                'button[id*="accept"]',
                'button.p-button:has-text("Accept")',
            ]
            
            for price_selector in price_change_selectors:
                try:
                    accept_btn = await page.query_selector(price_selector)
                    if accept_btn and await accept_btn.is_visible():
                        # Check if this is a price change modal by looking for price-related text
                        try:
                            modal_text = await page.query_selector('div[role="dialog"], div[class*="modal"]')
                            if modal_text:
                                text_content = await modal_text.inner_text()
                                text_lower = text_content.lower()
                                if any(keyword in text_lower for keyword in ['price', 'odds', 'changed', 'new', 'updated', 'different']):
                                    print(f"    ⚠️ [PRICE CHANGE] Detected price change modal - accepting new odds...")
                                    await accept_btn.click()
                                    await page.wait_for_timeout(1500)
                                    price_change_handled = True
                                    print(f"    ✓ Price change accepted!")
                                    break
                        except:
                            pass
                        
                        # Even if we can't verify it's a price modal, try clicking Accept
                        if not price_change_handled:
                            btn_text = await accept_btn.inner_text()
                            if 'accept' in btn_text.lower():
                                print(f"    ⚠️ [PRICE CHANGE] Found Accept button - clicking...")
                                await accept_btn.click()
                                await page.wait_for_timeout(1500)
                                price_change_handled = True
                                print(f"    ✓ Accepted!")
                                break
                except:
                    continue
            
            if price_change_handled:
                # After accepting price change, we need to wait for the bet to complete
                await page.wait_for_timeout(1000)
            
            # CRITICAL: Try all possible confirmation buttons
            confirmation_selectors = [
                'button:has-text("Confirm")',
                'button:has-text("Place Bet")',
                'button:has-text("OK")',
                'button:has-text("Yes")',
                'button[id*="confirm"]',
                'button[id*="place"]',
                'button[class*="confirm"]',
                'button[aria-label*="Confirm"]',
                'button.p-button:has-text("Confirm")',
            ]
            
            # Note: Betway doesn't always show a confirmation button - bet is placed automatically
            # Just wait for the bet to process
            await page.wait_for_timeout(1500)
            
            # ===== POST-BET VERIFICATION =====
            # Verify bet was actually placed using multiple checks
            print("  [POST-BET] Verifying bet placement...")
            verification = await verify_bet_placement(page, amount, balance_before)
            
            if verification['success']:
                confidence = verification['confidence']
                betslip_id = verification['betslip_id']
                booking_code = verification['booking_code']
                balance_after = verification['balance_after']
                
                # Display the most useful identifier found
                if booking_code:
                    print(f"    ✅ [VERIFIED-{confidence}] Bet placed! Booking Code: {booking_code}")
                elif betslip_id:
                    print(f"    ✅ [VERIFIED-{confidence}] Bet placed! Betslip ID: {betslip_id}")
                elif balance_after > 0 and verification['balance_decreased']:
                    print(f"    ✅ [VERIFIED-{confidence}] Bet placed! Balance: R{balance_before:.2f} → R{balance_after:.2f}")
                else:
                    print(f"    ✅ [VERIFIED-{confidence}] Bet appears to be placed")
                
                if confidence == 'LOW':
                    print(f"    ⚠️ WARNING: Low confidence verification - please check manually")
            else:
                print(f"    ❌ [VERIFY FAILED] Could not confirm bet was placed!")
                print(f"    ❌ Balance before: R{balance_before:.2f}, Balance after: R{verification['balance_after']:.2f}")
                # Don't return False immediately - let the old logic try as fallback
            
            # Look for success confirmation or "Continue betting" button
            # After successful bet, Betway shows a "Bet Confirmation" modal with "Continue betting" button
            continue_betting_selectors = [
                'button#strike-conf-continue-btn',  # Primary selector from HTML
                'button[aria-label="Continue betting"]',
                'button:has-text("Continue betting")',
                'button.p-button:has-text("Continue betting")',
            ]
            
            bet_confirmed = False
            for cont_selector in continue_betting_selectors:
                try:
                    continue_btn = await page.wait_for_selector(cont_selector, timeout=3000, state='visible')
                    if continue_btn:
                        print(f"    ✅ Found 'Continue betting' button - clicking to dismiss modal...")
                        await continue_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(300)
                        await continue_btn.evaluate('el => el.click()')
                        await page.wait_for_timeout(500)
                        
                        # Return based on verification result
                        if verification['success']:
                            print(f"    ✅ Bet CONFIRMED placed successfully!")
                            return True
                        else:
                            print(f"    ⚠️ Modal found but verification failed - treating as success with caution")
                            return True
                except:
                    continue
            
            # If verification succeeded but no continue button found, still return success
            if verification['success'] and verification['confidence'] in ['HIGH', 'MEDIUM']:
                print(f"    ✅ Bet verified without 'Continue betting' button (confidence: {verification['confidence']})")
                return True
            
            if bet_confirmed:
                return True
            
            # Alternative: Check for "Bet Confirmation" modal as success indicator
            try:
                bet_conf_modal = await page.query_selector('span:has-text("Bet Confirmation")')
                if bet_conf_modal:
                    print("    ✅ Found 'Bet Confirmation' modal - bet successful!")
                    # Try to close the modal using specific selectors only
                    close_selectors = [
                        'svg#modal-close-btn',  # Specific modal close button
                        'button#strike-conf-continue-btn',  # Continue betting button
                        'button[aria-label="Close"]',  # Exact match close button
                    ]
                    for close_sel in close_selectors:
                        try:
                            close_btn = await page.wait_for_selector(close_sel, timeout=2000, state='visible')
                            if close_btn:
                                # Verify it's not an account-related button
                                try:
                                    btn_text = await close_btn.inner_text()
                                    if 'deposit' in btn_text.lower() or 'account' in btn_text.lower():
                                        continue
                                except:
                                    pass
                                await close_btn.click()
                                await page.wait_for_timeout(1000)
                                print("    ✅ Closed confirmation modal")
                                return True
                        except:
                            continue
                    return True
            except:
                pass
            
            # Check for errors
            try:
                error_popup = await page.query_selector('div[class*="error"], div[role="alert"], div[class*="message"]')
                if error_popup:
                    error_text = await error_popup.inner_text()
                    if error_text and len(error_text.strip()) > 0:
                        error_lower = error_text.lower()
                        if 'conflict' in error_lower or 'related' in error_lower or 'same' in error_lower or 'error' in error_lower:
                            print(f"    ❌ [ERROR] Betway message: {error_text[:150]}")
                            return False
            except:
                pass
            
            # If we get here with no errors, assume success
            print("    ✅ No errors detected - bet likely placed successfully")
            return True
        except Exception as e:
            print(f"    [ERROR] Failed to place bet: {e}")
            return False
            
    except Exception as e:
        print(f"  Error placing bet slip: {e}")
        return False

async def wait_between_bets(page, seconds=5, add_random=True):
    """Wait for specified seconds between bets with optional randomization
    
    Handles page/browser closures gracefully by catching CancelledError
    """
    base_seconds = seconds
    
    if add_random:
        random_delay = random.randint(10, 60)
        total_seconds = base_seconds + random_delay
        print(f"\n[ANTI-DETECTION] Waiting {seconds} seconds + {random_delay}s random delay...")
    else:
        total_seconds = base_seconds
        print(f"\n[WAITING] {seconds} seconds before next bet...")
    
    chunk_size = 10
    chunks = total_seconds // chunk_size
    
    try:
        for i in range(chunks):
            # Check if page is still valid before sleeping
            if page.is_closed():
                print("[ERROR] Page was closed during wait!")
                return False
            
            try:
                await asyncio.sleep(chunk_size)
            except asyncio.CancelledError:
                print(f"\n[WARNING] Wait interrupted at {(i + 1) * chunk_size}s - page/browser may have been closed")
                return False
            
            elapsed = (i + 1) * chunk_size
            remaining = total_seconds - elapsed
            if i % 6 == 0:
                print(f"  [{elapsed}s elapsed, {remaining}s remaining]")
        
        remainder = total_seconds % chunk_size
        if remainder > 0:
            try:
                await asyncio.sleep(remainder)
            except asyncio.CancelledError:
                print(f"\n[WARNING] Wait interrupted during final {remainder}s - page/browser may have been closed")
                return False
        
        print("[OK] Wait complete!\n")
        return True
        
    except asyncio.CancelledError:
        print("\n[ERROR] Wait operation cancelled - likely due to browser/page closure")
        return False

async def main_async(num_matches=None, amount_per_slip=None, min_gap_hours=2.0):
    """Main async function to run the Betway automation
    
    Args:
        num_matches: Number of matches to bet on (default: prompts user)
        amount_per_slip: Amount to bet per slip in Rand (default: prompts user)
        min_gap_hours: Minimum gap between matches in hours (default: 2.0)
    """
    # Start timer
    import time
    script_start_time = time.time()
    
    async with async_playwright() as p:
        print("Starting Betway Automation...")
        print("="*60)
        
        # Prompt user for parameters if not provided
        if num_matches is None:
            while True:
                try:
                    num_matches = int(input("\nHow many matches per bet slip? (e.g., 3): ").strip())
                    if num_matches > 0:
                        total_slips = 3 ** num_matches
                        print(f"\nThis will create {total_slips} bet slips (each with {num_matches} matches)")
                        confirm = input("Continue? (yes/no): ").strip().lower()
                        if confirm == "yes":
                            break
                        else:
                            continue
                    else:
                        print("Please enter a positive number.")
                except ValueError:
                    print("Invalid input. Please enter a number.")
        
        if amount_per_slip is None:
            while True:
                try:
                    amount_per_slip = float(input("\nHow much per bet slip? (e.g., 1.0): R ").strip())
                    if amount_per_slip > 0:
                        total_cost = (3 ** num_matches) * amount_per_slip
                        print(f"\nTotal cost: R{total_cost:.2f}")
                        confirm = input("Continue? (yes/no): ").strip().lower()
                        if confirm == "yes":
                            break
                        else:
                            continue
                    else:
                        print("Please enter a positive amount.")
                except ValueError:
                    print("Invalid input. Please enter a number.")
        
        # Calculate dynamic min_time_before_match based on estimated runtime
        # Formula: (3^num_matches) * (7/3) minutes = estimated runtime
        # Add 30 minutes buffer for safety
        total_combinations = 3 ** num_matches
        estimated_runtime_minutes = total_combinations * (7 / 3)  # ~2.33 min per bet
        buffer_minutes = 30  # Safety buffer
        min_time_before_match = math.ceil((estimated_runtime_minutes + buffer_minutes) / 60)
        
        print(f"\n📊 Dynamic timing calculated:")
        print(f"   Total combinations: {total_combinations}")
        print(f"   Estimated runtime: {estimated_runtime_minutes:.0f} minutes")
        print(f"   Safety buffer: {buffer_minutes} minutes")
        print(f"   ➡️  First match must start in: {min_time_before_match}+ hours")
        
        # Login with retry
        result = await retry_with_backoff(login_to_betway, max_retries=3, initial_delay=5, playwright=p)
        page = result["page"]
        browser = result["browser"]
        
        # Check for existing progress file FIRST (before scraping)
        progress_file = 'bet_progress.json'
        resume_data = None
        saved_matches = None  # Will hold saved match data if resuming
        skip_scraping = False
        
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r') as f:
                    resume_data = json.load(f)
                    print(f"\n{'='*60}")
                    print(f"📋 FOUND EXISTING PROGRESS FILE")
                    print(f"{'='*60}")
                    print(f"Last completed bet: {resume_data.get('last_completed_bet', 0)}")
                    print(f"Successful: {resume_data.get('successful', 0)} | Failed: {resume_data.get('failed', 0)}")
                    
                    # Check if we have saved match data
                    if 'matches_data' in resume_data:
                        saved_timestamp = resume_data.get('timestamp', '')
                        if saved_timestamp:
                            try:
                                saved_time = datetime.fromisoformat(saved_timestamp)
                                time_since_save = datetime.now() - saved_time
                                hours_since_save = time_since_save.total_seconds() / 3600
                                minutes_since_save = time_since_save.total_seconds() / 60
                                
                                if hours_since_save > 2:
                                    print(f"⚠️ Progress is {hours_since_save:.1f} hours old - will re-scrape")
                                elif hours_since_save > 1:
                                    print(f"⚠️ Progress is {minutes_since_save:.0f} minutes old - will validate matches")
                                    saved_matches = resume_data.get('matches_data', [])
                                    skip_scraping = True  # Try to use saved matches, validate later
                                else:
                                    print(f"✅ Progress is {minutes_since_save:.0f} minutes old - will reuse saved matches")
                                    saved_matches = resume_data.get('matches_data', [])
                                    skip_scraping = True
                            except Exception as e:
                                print(f"⚠️ Could not parse timestamp: {e}")
                    
                    print(f"{'='*60}\n")
            except Exception as e:
                print(f"\n⚠️ [WARNING] Could not read progress file: {e}")
                print(f"[ACTION] Starting fresh...\n")
                resume_data = None
        
        print(f"\nParameters:")
        print(f"  Matches per slip: {num_matches}")
        print(f"  Amount per slip: R{amount_per_slip}")
        print(f"  Strategy: All possible combinations (3 outcomes per match)")
        print(f"  Total bets: {3**num_matches} ({3**num_matches} combinations)")
        print(f"  Minimum gap between matches: {min_gap_hours} hours")
        print(f"  Minimum time before match: {min_time_before_match} hours")
        print(f"  Anti-Detection: 5s waits + random delays + browser restarts")
        print("="*60)
        
        # Navigate to upcoming matches page
        print("\nNavigating to upcoming matches page...")
        try:
            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(3000)
            try:
                await close_all_modals(page)
            except:
                pass  # Non-critical if modal closing fails
        except Exception as nav_error:
            print(f"[ERROR] Failed to navigate to matches page: {nav_error}")
            await browser.close()
            return
        
        # Define parse_match_time function early (needed for both resume and fresh scraping)
        def parse_match_time(match):
            """Parse match start time and return minutes from midnight"""
            start_time_text = match.get('start_time', '')
            
            # Future dates - add day offset
            if re.search(r'\d{1,2}\s+\w{3}', start_time_text):
                time_match = re.search(r'(\d{1,2}):(\d{2})', start_time_text)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    return 1440 + (hour * 60) + minute
                return None
            
            # Tomorrow matches
            if 'Tomorrow' in start_time_text:
                time_match = re.search(r'(\d{1,2}):(\d{2})', start_time_text)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    return 1440 + (hour * 60) + minute
                return None
            
            # Today matches
            if 'Today' in start_time_text:
                time_match = re.search(r'(\d{1,2}):(\d{2})', start_time_text)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    return (hour * 60) + minute
            
            return None
        
        # Define min_gap_minutes early (needed for both resume and fresh scraping)
        min_gap_minutes = int(min_gap_hours * 60)
        
        # Check if we can skip scraping and use saved matches
        filtered_matches = []
        
        if skip_scraping and saved_matches and len(saved_matches) >= num_matches:
            print(f"\n{'='*60}")
            print("⚡ USING SAVED MATCH DATA (SKIPPING SCRAPING)")
            print(f"{'='*60}")
            print(f"Found {len(saved_matches)} saved matches from previous run")
            
            # Validate saved matches are still valid (not started yet)
            now = datetime.now()
            current_minutes = now.hour * 60 + now.minute
            valid_matches = []
            
            for match in saved_matches:
                match_time = parse_match_time(match)
                if match_time is not None:
                    # Check if match hasn't started yet (with 30 min buffer)
                    if match_time > current_minutes + 30:
                        valid_matches.append(match)
                        print(f"  ✓ {match['name']} ({match.get('start_time')}) - still valid")
                    else:
                        print(f"  ❌ {match['name']} ({match.get('start_time')}) - already started or too soon")
            
            if len(valid_matches) >= num_matches:
                filtered_matches = valid_matches[:num_matches]
                print(f"\n✅ Using {len(filtered_matches)} saved matches - no scraping needed!")
                print(f"{'='*60}\n")
            else:
                print(f"\n⚠️ Not enough valid saved matches ({len(valid_matches)} < {num_matches})")
                print(f"[ACTION] Will scrape for fresh matches...\n")
                skip_scraping = False
                saved_matches = None
        
        # Only scrape if we don't have valid saved matches
        if not filtered_matches:
            # Find matches that start in specified hours by clicking "Next" until we find them
            print(f"\nSearching for matches starting in {min_time_before_match}+ hours...")
            target_hours = min_time_before_match
            now = datetime.now()
            target_time = now + timedelta(hours=target_hours)
            target_hour = target_time.hour
            target_minute = target_time.minute
            
            print(f"Current time: {now.hour:02d}:{now.minute:02d}")
            print(f"Looking for matches around: {target_hour:02d}:{target_minute:02d} or later")
            
            # ============================================================================
            # SMART SCRAPING - Only scrape matches that meet conditions, stop when done
            # ============================================================================
            print(f"\n{'='*60}")
            print("🔍 STARTING SMART SCRAPING")
            print(f"{'='*60}")
            print(f"Looking for {num_matches} matches that:")
            print(f"  1. Start {min_time_before_match}+ hours from now")
            print(f"  2. Are {min_gap_hours}+ hours apart from each other")
            print(f"  3. Have valid URLs captured")
            print(f"Stopping as soon as we find {num_matches} matches meeting all conditions")
            print(f"{'='*60}\n")
        
            min_gap_minutes = int(min_gap_hours * 60)
            max_pages = 20
            current_page = 0
            
            while len(filtered_matches) < num_matches and current_page < max_pages:
                current_page += 1
                print(f"\n📄 Scraping page {current_page}/{max_pages}...")
                
                await close_all_modals(page)
                await page.wait_for_timeout(500)
                
                # Scroll to load content
                for _ in range(3):
                    await page.evaluate('window.scrollBy(0, 500)')
                    await page.wait_for_timeout(200)
                
                match_containers = await page.query_selector_all('div[data-v-206d232b].relative.grid.grid-cols-12')
                print(f"  Found {len(match_containers)} match containers on page {current_page}")
                
                # Track which matches we've already checked on this page (by name)
                processed_match_names = set()
                
                # Debug counters
                debug_no_teams = 0
                debug_no_time = 0
                debug_live = 0
                debug_too_soon = 0
                debug_no_odds = 0
                debug_wrong_odds_count = 0
                debug_no_gap = 0
                
                # Keep processing containers until we've checked them all or found enough matches
                while True:
                    # Process each match container
                    found_match_on_page = False
                    
                    for i, container in enumerate(match_containers):
                        if len(filtered_matches) >= num_matches:
                            print(f"\n✅ Found {num_matches} matches - stopping scraping early")
                            break
                        
                        try:
                            # Extract team names first to check if already processed
                            team_elements = await container.query_selector_all('strong.overflow-hidden.text-ellipsis')
                            if len(team_elements) < 2:
                                debug_no_teams += 1
                                continue
                            
                            team1 = await team_elements[0].inner_text()
                            team2 = await team_elements[1].inner_text()
                            match_name = f"{team1} vs {team2}"
                            
                            # Skip if already processed this match
                            if match_name in processed_match_names:
                                continue
                            
                            # Mark as processed
                            processed_match_names.add(match_name)
                            
                            # Extract start time
                            start_time_text = None
                            all_spans = await container.query_selector_all('span')
                            for span in all_spans:
                                try:
                                    span_text = await span.inner_text()
                                    if span_text and (
                                        re.match(r'(Today|Tomorrow|Mon|Tue|Wed|Thu|Fri|Sat|Sun).*\d{1,2}:\d{2}', span_text) or
                                        re.match(r'\d{1,2}\s+\w{3}\s*-?\s*\d{1,2}:\d{2}', span_text)
                                    ):
                                        start_time_text = span_text
                                        break
                                except:
                                    continue
                            
                            if not start_time_text:
                                debug_no_time += 1
                                continue
                            
                            # Skip live matches
                            if 'Live' in start_time_text or 'live' in start_time_text.lower():
                                debug_live += 1
                                continue
                            
                            # Check if match meets basic time requirement (min_time_before_match hours or future date)
                            is_valid_time = False
                            
                            # Accept future dates
                            if re.search(r'\d{1,2}\s+\w{3}', start_time_text):
                                is_valid_time = True
                            # Accept tomorrow matches
                            elif 'Tomorrow' in start_time_text:
                                is_valid_time = True
                            # For today's matches, check if they meet minimum time requirement
                            elif 'Today' in start_time_text:
                                time_match = re.search(r'(\d{1,2}):(\d{2})', start_time_text)
                                if time_match:
                                    start_hour = int(time_match.group(1))
                                    start_minute = int(time_match.group(2))
                                    time_until_match = (start_hour - now.hour) * 60 + (start_minute - now.minute)
                                    
                                    min_minutes = int(min_time_before_match * 60)
                                    if time_until_match >= min_minutes:
                                        is_valid_time = True
                            
                            if not is_valid_time:
                                debug_too_soon += 1
                                continue
                            
                            # Extract odds
                            odds = []
                            try:
                                price_divs = await container.query_selector_all('div[price]')
                                if len(price_divs) >= 3:
                                    for j in range(3):
                                        btn = price_divs[j]
                                        try:
                                            odd_elem = await btn.query_selector('span')
                                            if odd_elem:
                                                odd_text = await odd_elem.inner_text()
                                                if odd_text and odd_text.replace('.', '').replace(',', '').isdigit():
                                                    odds.append(float(odd_text.replace(',', '.')))
                                        except:
                                            continue
                                else:
                                    debug_no_odds += 1
                            except:
                                debug_no_odds += 1
                            
                            # CRITICAL: Only accept matches with exactly 3 odds (1X2 market)
                            # This filters for 1X2 matches without checking text headers
                            if len(odds) != 3:
                                debug_wrong_odds_count += 1
                                continue  # Skip matches without 1X2 market
                            
                            # Create match object
                            match = {
                                'name': match_name,
                                'team1': team1,
                                'team2': team2,
                                'odds': odds[:3],
                                'start_time': start_time_text,
                                'url': None
                            }
                            
                            # Check if this match is at least 2 hours apart from all already selected matches
                            current_time = parse_match_time(match)
                            if current_time is None:
                                continue
                            
                            is_far_enough = True
                            
                            for selected_match in filtered_matches:
                                selected_time = parse_match_time(selected_match)
                                if selected_time is not None:
                                    time_diff = abs(current_time - selected_time)
                                    if time_diff < min_gap_minutes:
                                        is_far_enough = False
                                        break
                            
                            if not is_far_enough:
                                debug_no_gap += 1
                                continue
                            
                            # Try to capture URL for this match - extract from href attribute instead of navigating
                            try:
                                # Find the <a> element that contains the match URL
                                link_element = await container.query_selector('a[href*="/event/soccer/"]')
                                if link_element:
                                    try:
                                        # Extract href attribute directly - no navigation needed!
                                        relative_url = await link_element.get_attribute('href')
                                        if relative_url:
                                            # Convert relative URL to absolute
                                            if relative_url.startswith('/'):
                                                match_url = f"https://new.betway.co.za{relative_url}"
                                            else:
                                                match_url = relative_url
                                        else:
                                            match_url = None
                                    except Exception as href_error:
                                        print(f"  ⚠️ Failed to extract href: {href_error}")
                                        match_url = None
                                    
                                    # No need for page.go_back() or close_all_modals() anymore!
                                    match['url'] = match_url
                                    filtered_matches.append(match)
                                    print(f"  ✓ Match {len(filtered_matches)}/{num_matches}: '{match_name}' ({start_time_text}) [URL cached]")
                                    
                                    # No need to re-query containers since we didn't navigate away!
                                    found_match_on_page = True
                                    
                                    if len(filtered_matches) >= num_matches:
                                        break  # We have enough matches
                                        
                                else:
                                    print(f"  ⚠️ Could not find link element for '{match_name}'")
                            except Exception as e:
                                print(f"  ⚠️ Could not capture URL for '{match_name}': {e}")
                                continue
                            
                        except Exception as e:
                            continue
                    
                    # If we didn't find a match, we've processed all containers
                    if not found_match_on_page:
                        break
                    
                    # If we have enough matches, stop
                    if len(filtered_matches) >= num_matches:
                        break
                
                # Print debug info for this page
                if len(filtered_matches) < num_matches:
                    print(f"  📊 Debug - why matches were skipped on page {current_page}:")
                    if debug_no_teams > 0:
                        print(f"    ❌ {debug_no_teams} - Missing team names")
                    if debug_no_time > 0:
                        print(f"    ❌ {debug_no_time} - No start time found")
                    if debug_live > 0:
                        print(f"    ❌ {debug_live} - Live matches (excluded)")
                    if debug_too_soon > 0:
                        print(f"    ❌ {debug_too_soon} - Starts too soon (<{min_time_before_match} hours)")
                    if debug_no_odds > 0:
                        print(f"    ❌ {debug_no_odds} - No odds available")
                    if debug_wrong_odds_count > 0:
                        print(f"    ❌ {debug_wrong_odds_count} - Not 1X2 market (odds ≠ 3)")
                    if debug_no_gap > 0:
                        print(f"    ❌ {debug_no_gap} - Too close to other selected matches (<{min_gap_hours}h gap)")
                
                # Early termination check
                if len(filtered_matches) >= num_matches:
                    break
                
                # Click Next button if we need more matches and haven't reached max pages
                if len(filtered_matches) < num_matches and current_page < max_pages:
                    print(f"  Need {num_matches - len(filtered_matches)} more match(es) - moving to page {current_page + 1}...")
                    try:
                        # Try multiple Next button selectors
                        next_button = None
                        next_selectors = [
                            'button[aria-label="Go to next page"]',
                            'button.p-ripple.p-element.p-paginator-next',
                            'button:has-text("Next")',
                            'button[class*="next"]'
                        ]
                        
                        for selector in next_selectors:
                            try:
                                btn = await page.query_selector(selector)
                                if btn:
                                    is_disabled = await btn.get_attribute('disabled')
                                    if not is_disabled:
                                        next_button = btn
                                        break
                            except:
                                continue
                        
                        if next_button:
                            print(f"  Clicking 'Next' to load page {current_page + 1}...")
                            await next_button.click()
                            await page.wait_for_timeout(1500)
                        else:
                            # No next button found or all disabled - we've reached the end
                            print(f"  No 'Next' button available - reached last page")
                            break  # Exit outer while loop - no more pages
                            
                    except Exception as e:
                        print(f"  ⚠️ Error clicking Next button: {e}")
                        # Don't break - try to continue anyway by reloading or continuing
                        print(f"  Attempting to continue to page {current_page + 1} anyway...")
                        # The outer loop will continue and try to get containers on what might be the same page
        
            print(f"\n{'='*60}")
            print(f"✅ SMART SCRAPING COMPLETE")
            print(f"{'='*60}")
            print(f"Found {len(filtered_matches)}/{num_matches} matches meeting all conditions")
            print(f"Scraped {current_page} pages")
            print(f"{'='*60}\n")
            
            if len(filtered_matches) < num_matches:
                print(f"\n[ERROR] Could not find {num_matches} matches with {min_gap_hours}+ hour gaps")
                print(f"Scraped {current_page} pages, found {len(filtered_matches)} matches meeting conditions")
                await browser.close()
                return
        
        # Use the filtered matches
        matches = filtered_matches[:num_matches]
        print(f"\n{'='*60}")
        print(f"📋 SELECTED MATCHES ({len(matches)} matches)")
        print(f"{'='*60}")
        for i, m in enumerate(matches, 1):
            start_time = m.get('start_time', 'Unknown time')
            odds = m.get('odds', [])
            odds_str = f"1:{odds[0]:.2f} X:{odds[1]:.2f} 2:{odds[2]:.2f}" if len(odds) >= 3 else str(odds)
            print(f"  {i}. {m['name']}")
            print(f"     ⏰ Start: {start_time}")
            print(f"     📊 Odds: {odds_str}")
        print(f"{'='*60}")
        print(f"All matches are {min_gap_hours}+ hours apart from each other")
        print(f"{'='*60}\n")
        
        # CRITICAL: Validate all matches have cached URLs before proceeding
        print(f"\n{'='*60}")
        print("URL VALIDATION: Checking all matches have cached URLs")
        print(f"{'='*60}")
        
        missing_urls = []
        for i, match in enumerate(matches, 1):
            match_url = match.get('url')
            start_time = match.get('start_time', 'Unknown')
            if match_url:
                print(f"  ✓ Match {i}: {match['name']} ({start_time}) - URL cached")
            else:
                print(f"  ❌ Match {i}: {match['name']} ({start_time}) - NO URL!")
                missing_urls.append(match['name'])
        
        if missing_urls:
            print(f"\n{'='*60}")
            print(f"❌ URL VALIDATION FAILED!")
            print(f"{'='*60}")
            print(f"The following {len(missing_urls)} match(es) are missing cached URLs:")
            for match_name in missing_urls:
                print(f"  - {match_name}")
            print(f"\n⛔ CANNOT PROCEED - All matches must have cached URLs for bet placement")
            print(f"This usually happens if the match page failed to load during scraping.")
            print(f"Please try running the script again.")
            print(f"{'='*60}")
            await browser.close()
            return
        
        print(f"\n✅ All {len(matches)} matches have valid cached URLs!")
        print(f"{'='*60}\n")
        
        print(f"\n{'='*60}")
        print("✅ SCRAPING PHASE COMPLETE")
        print(f"{'='*60}")
        print(f"Successfully scraped {len(matches)} matches with cached URLs")
        print(f"All matches validated and ready for bet placement")
        print(f"{'='*60}\n")
        
        # Runtime validation to verify matches meet minimum gap requirement
        print(f"\n{'='*60}")
        print(f"RUNTIME VALIDATION: Verifying {min_gap_hours}+ hour gaps between matches")
        print(f"{'='*60}")
        
        validation_failed = False
        for i in range(len(matches) - 1):
            current_time = parse_match_time(matches[i])
            next_time = parse_match_time(matches[i + 1])
            
            if current_time is not None and next_time is not None:
                time_gap = abs(next_time - current_time)
                hours_gap = time_gap / 60
                
                print(f"\nMatch {i+1} ({matches[i].get('start_time')}) → Match {i+2} ({matches[i+1].get('start_time')})")
                print(f"  Time gap: {time_gap} minutes ({hours_gap:.2f} hours)")
                
                if time_gap < min_gap_minutes:
                    print(f"  ❌ ERROR: Gap is less than {min_gap_hours} hours!")
                    validation_failed = True
                else:
                    print(f"  ✓ OK: Gap is {min_gap_hours}+ hours")
        
        if validation_failed:
            print(f"\n{'='*60}")
            print(f"VALIDATION FAILED: Matches are NOT {min_gap_hours}+ hours apart!")
            print("Aborting to prevent incorrect bets.")
            print(f"{'='*60}")
            await browser.close()
            return
        
        print(f"\n✓ All matches verified to be {min_gap_hours}+ hours apart - safe to proceed!")
        
        # Verify we're on the matches page (we should be after scraping)
        print(f"\n[IMPORTANT] Verifying we're on the upcoming matches page...")
        current_url = page.url
        if 'soccer' not in current_url.lower():
            print(f"Current URL: {current_url}")
            print("Navigating to upcoming matches page...")
            try:
                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=30000)
                await page.wait_for_timeout(2000)
                await close_all_modals(page)
                print("[OK] Back on upcoming matches page - ready to place bets")
            except Exception as e:
                print(f"[WARNING] Could not navigate to matches page: {e}")
                print("Attempting to continue anyway...")
        else:
            print(f"[OK] Already on matches page - ready to place bets")
        
        # Final time validation
        print(f"\n{'='*60}")
        print("FINAL TIME VALIDATION")
        print(f"{'='*60}")
        
        total_bets = num_matches ** 3
        avg_time_per_bet = 7 / 3  # Based on: 3 combinations takes ~7 minutes
        total_time_needed = total_bets * avg_time_per_bet
        estimated_hours = math.ceil(total_time_needed / 60)
        
        print(f"Total bets to place: {total_bets}")
        print(f"Estimated time per bet: ~{avg_time_per_bet:.2f} minutes")
        print(f"Total time needed: ~{total_time_needed:.0f} minutes (~{estimated_hours} hour(s))")
        
        first_match = matches[0]
        first_match_start_time = first_match.get('start_time', '')
        
        # Calculate completion time based on first match start time
        if first_match_start_time:
            print(f"\nFirst match: {first_match['name']} | ⏰ {first_match_start_time}")
            
            # Parse first match start time to calculate deadline
            first_match_minutes = parse_match_time(first_match)
            if first_match_minutes is not None:
                now = datetime.now()
                current_minutes = now.hour * 60 + now.minute
                
                # Handle tomorrow/future dates (parse_match_time adds 1440 for next day)
                if first_match_minutes >= 1440:
                    # Tomorrow or future - calculate time remaining
                    minutes_until_match = first_match_minutes - current_minutes
                else:
                    minutes_until_match = first_match_minutes - current_minutes
                
                hours_until_match = minutes_until_match / 60
                
                # Calculate if we have enough time
                if minutes_until_match > total_time_needed:
                    buffer_minutes = minutes_until_match - total_time_needed
                    buffer_hours = buffer_minutes / 60
                    print(f"\n✅ TIME CHECK:")
                    print(f"   Time until first match: {minutes_until_match:.0f} min ({hours_until_match:.1f} hours)")
                    print(f"   Estimated script runtime: {total_time_needed:.0f} min (~{estimated_hours} hour(s))")
                    print(f"   Buffer before match: {buffer_minutes:.0f} min ({buffer_hours:.1f} hours)")
                    print(f"\n[OK] Time validated - safe to proceed!")
                else:
                    print(f"\n⚠️ TIME WARNING:")
                    print(f"   Time until first match: {minutes_until_match:.0f} min ({hours_until_match:.1f} hours)")
                    print(f"   Estimated script runtime: {total_time_needed:.0f} min (~{estimated_hours} hour(s))")
                    print(f"   Script may not complete before match starts!")
                    print(f"\n[WARNING] Proceeding anyway - watch timing carefully!")
            else:
                print(f"\n[OK] Time validated - safe to proceed!")
        
        print(f"{'='*60}\n")
        
        # Generate all possible combinations (3^num_matches total)
        bet_slips = generate_bet_combinations(matches, num_matches)
        
        print(f"\n{'='*60}")
        print("BET COMBINATION SUMMARY & VALIDATION")
        print(f"{'='*60}")
        print(f"Total combinations: {len(bet_slips)} (3^{num_matches})")
        print(f"✅ All combinations generated successfully")
        
        # VALIDATE: Show first 5 combinations as examples
        print(f"\nFirst 5 combinations (examples):")
        for i, slip in enumerate(bet_slips[:5], 1):
            selections_str = ', '.join([f"{m['name'][:20]}→{s}" for m, s in zip(slip['matches'], slip['selections'])])
            print(f"  {i}. {selections_str}")
        
        if len(bet_slips) > 5:
            print(f"  ... ({len(bet_slips) - 5} more combinations)")
        
        print(f"\n✅ VALIDATION: All {len(bet_slips)} combinations are valid and ready")
        print(f"{'='*60}\n")
        
        print(f"\n{'='*60}")
        print("🚀 STARTING BET PLACEMENT PHASE")
        print(f"{'='*60}")
        print(f"Total bets to place: {len(bet_slips)}")
        print(f"Using cached URLs for all {num_matches} matches")
        print(f"Amount per bet: R{amount_per_slip:.2f}")
        print(f"Total amount: R{len(bet_slips) * amount_per_slip:.2f}")
        print(f"{'='*60}\n")
        
        # Process ALL bets with enhanced anti-detection and progress tracking
        successful = 0
        failed = 0
        failed_bets = []  # Track failed bets for retry
        start_index = 0  # Track where to continue from
        
        # Initialize match position cache for faster bet placement
        match_cache = {}
        
        # Initialize outcome button cache to reuse buttons across all bets
        outcome_button_cache = {}
        
        # PRE-CACHE: Navigate to all match pages and cache outcome buttons ONCE
        print(f"\n{'='*60}")
        print("🔄 PRE-CACHING OUTCOME BUTTONS FOR ALL MATCHES")
        print(f"{'='*60}")
        print(f"Navigating to {num_matches} match pages to cache buttons...")
        print(f"Cache will be PERSISTENT across all {len(bet_slips)} bet combinations")
        print(f"Cache is NEVER cleared - used for entire script run")
        print(f"{'='*60}\n")
        
        for match_idx, match in enumerate(matches[:num_matches], 1):
            match_url = match.get('url')
            start_time = match.get('start_time', 'Unknown time')
            if match_url and match_url not in outcome_button_cache:
                try:
                    print(f"Match {match_idx}/{num_matches}: {match['name']} | ⏰ {start_time}")
                    print(f"  Navigating to: {match_url}")
                    await page.goto(match_url, wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(1500)
                    await close_all_modals(page)
                    await page.wait_for_timeout(500)
                    
                    # Find working selector for outcome buttons
                    button_selectors = [
                        'div.grid.p-1 > div.flex.items-center.justify-between.h-12',
                        'div[class*="grid"] > div[class*="flex items-center justify-between h-12"]',
                        'details:has(span:text("1X2")) div.grid > div',
                        'div[price]',
                        'button[data-translate-market-name="Full Time Result"] div[price]',
                        'div[data-translate-market-name="Full Time Result"] div[price]',
                    ]
                    
                    working_selector = None
                    for selector in button_selectors:
                        try:
                            buttons = await page.query_selector_all(selector)
                            if len(buttons) >= 3:
                                working_selector = selector
                                print(f"  ✓ Found {len(buttons)} outcome buttons using selector: {selector}")
                                break
                        except:
                            continue
                    
                    if working_selector:
                        # Cache the selector, not the elements
                        outcome_button_cache[match_url] = working_selector
                        print(f"  ✓ [CACHED] Selector stored for reuse across all {len(bet_slips)} bets\n")
                    else:
                        print(f"  ❌ ERROR: Could not find working selector\n")
                        
                except Exception as e:
                    print(f"  ❌ ERROR caching buttons: {e}\n")
        
        print(f"{'='*60}")
        print(f"✅ PRE-CACHING COMPLETE")
        print(f"{'='*60}")
        print(f"Cached outcome buttons for {len(outcome_button_cache)}/{num_matches} matches")
        print(f"Cache is PERSISTENT - all {len(bet_slips)} bets will reuse cached data")
        print(f"No cache clearing - preserved for entire script execution")
        print(f"{'='*60}\n")
        
        # Navigate back to soccer page before starting bet placement
        await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
        await page.wait_for_timeout(1000)
        await close_all_modals(page)
        
        # Create a match fingerprint to validate matches haven't changed
        current_match_fingerprint = []
        for match in matches:
            fingerprint = f"{match['team1']}|{match['team2']}|{match.get('start_time', 'unknown')}"
            current_match_fingerprint.append(fingerprint)
        
        # Check if resuming with saved progress
        if resume_data:
            saved_fingerprint = resume_data.get('match_fingerprint', [])
            saved_timestamp = resume_data.get('timestamp', '')
            
            # Check if progress is too old (more than 2 hours)
            is_expired = False
            if saved_timestamp:
                try:
                    saved_time = datetime.fromisoformat(saved_timestamp)
                    time_since_save = datetime.now() - saved_time
                    hours_since_save = time_since_save.total_seconds() / 3600
                    
                    if hours_since_save > 2:
                        print(f"\n⚠️ [WARNING] Progress file is {hours_since_save:.1f} hours old!")
                        print(f"[INFO] Saved at: {saved_timestamp}")
                        print(f"[ACTION] Progress expired - starting fresh...\n")
                        is_expired = True
                except Exception as e:
                    print(f"\n⚠️ [WARNING] Could not parse timestamp: {e}")
                    is_expired = True
            
            # Validate matches haven't changed
            if is_expired:
                os.remove(progress_file)
                resume_data = None
            elif saved_fingerprint != current_match_fingerprint:
                print(f"\n⚠️ [WARNING] Matches have changed since last run!")
                print(f"[INFO] Saved matches: {saved_fingerprint}")
                print(f"[INFO] Current matches: {current_match_fingerprint}")
                print(f"[ACTION] Deleting progress file and starting fresh...\n")
                os.remove(progress_file)
                resume_data = None  # Clear resume data
            else:
                start_index = resume_data.get('last_completed_bet', 0)
                successful = resume_data.get('successful', 0)
                failed = 0  # Reset failed count since we're retrying
                
                # Calculate time since last save
                time_info = ""
                if saved_timestamp:
                    try:
                        saved_time = datetime.fromisoformat(saved_timestamp)
                        minutes_ago = (datetime.now() - saved_time).total_seconds() / 60
                        time_info = f" ({minutes_ago:.0f} minutes ago)"
                    except:
                        pass
                
                print(f"\n✅ [RESUME] Matches validated - same as previous run")
                print(f"[RESUME] Resuming from bet {start_index + 1}/{len(bet_slips)} (retrying failed bet)")
                print(f"[PROGRESS] Previously successful: {successful}{time_info}\n")
        
        for i, bet_slip in enumerate(bet_slips):
            # Skip already completed bets
            if i < start_index:
                continue
                
            print(f"\n{'='*60}")
            print(f"BET {i+1}/{len(bet_slips)}")
            print(f"{'='*60}")
            
            # Check if still logged in before each bet
            is_logged_in = await check_and_relogin(page, browser)
            if not is_logged_in:
                print(f"\n❌ [FATAL] Could not verify/restore login - stopping bet placement")
                print(f"[INFO] Completed {successful} bets before login failure")
                # Save progress before stopping
                with open(progress_file, 'w') as f:
                    json.dump({
                        'last_completed_bet': i,
                        'last_successful_bet': i - 1 if i > 0 else 0,
                        'successful': successful,
                        'failed': failed,
                        'match_fingerprint': current_match_fingerprint,
                        'timestamp': datetime.now().isoformat(),
                        'matches_data': matches
                    }, f)
                break
            
            try:
                # Try to place bet with retry on network errors
                try:
                    success = await retry_with_backoff(
                        place_bet_slip,
                        max_retries=3,
                        initial_delay=5,
                        page=page, bet_slip=bet_slip, amount=amount_per_slip, match_cache=match_cache, outcome_button_cache=outcome_button_cache
                    )
                except (PlaywrightError, PlaywrightTimeoutError) as e:
                    # Network failure - mark bet as failed
                    print(f"\n[CRITICAL ERROR] Network failure after retries: {e}")
                    print("[ERROR] Marking bet as failed (no browser restart)")
                    success = False
                
                if success == True:
                    successful += 1
                    print(f"\n[SUCCESS] Bet slip {bet_slip['slip_number']} placed!")
                    
                    # Save progress - track last SUCCESSFUL bet index
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i + 1,  # Next bet to attempt
                            'last_successful_bet': i,  # Last successful bet index
                            'successful': successful,
                            'failed': failed,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat(),
                            'matches_data': matches  # Save match data for resume
                        }, f)
                    
                    # AGGRESSIVE memory management to prevent Playwright corruption
                    # GC every bet (not just every 3) for more stable long sessions
                    gc.collect()
                    
                    # BROWSER RESTART every 10 bets - TRUE fix for Playwright memory corruption
                    # Creates completely fresh browser instance with clean memory state
                    if (i + 1) % 10 == 0 and i < len(bet_slips) - 1:
                        try:
                            print(f"\n  [BROWSER RESTART] Restarting browser after {i + 1} bets to prevent memory corruption...")
                            restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                            if restart_result:
                                page = restart_result["page"]
                                browser = restart_result["browser"]
                                # Clear outcome button cache since we have a fresh browser
                                outcome_button_cache.clear()
                                print(f"  [BROWSER RESTART] ✓ Fresh browser ready - cache cleared")
                                
                                # Re-cache outcome buttons for all matches
                                print(f"  [BROWSER RESTART] Re-caching outcome buttons for {num_matches} matches...")
                                for match_idx, match in enumerate(matches[:num_matches], 1):
                                    match_url = match.get('url')
                                    if match_url:
                                        try:
                                            await page.goto(match_url, wait_until='domcontentloaded', timeout=15000)
                                            await page.wait_for_timeout(1000)
                                            await close_all_modals(page)
                                            
                                            button_selectors = [
                                                'div.grid.p-1 > div.flex.items-center.justify-between.h-12',
                                                'div[price]',
                                            ]
                                            for selector in button_selectors:
                                                buttons = await page.query_selector_all(selector)
                                                if len(buttons) >= 3:
                                                    outcome_button_cache[match_url] = selector
                                                    print(f"       ✓ Match {match_idx}: cached")
                                                    break
                                        except Exception as cache_err:
                                            print(f"       ⚠️ Match {match_idx}: cache failed - {cache_err}")
                                
                                # Navigate back to soccer page
                                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                await page.wait_for_timeout(1000)
                                await close_all_modals(page)
                            else:
                                print(f"  [BROWSER RESTART] ⚠️ Failed - continuing with current browser")
                        except Exception as restart_err:
                            print(f"  [BROWSER RESTART] ⚠️ Error: {restart_err} - continuing with current browser")
                    
                    # Periodic page refresh (every 5 bets that aren't browser restart bets)
                    elif (i + 1) % 5 == 0 and i < len(bet_slips) - 1:
                        try:
                            print(f"  [MEMORY] Refreshing page after {i + 1} bets...")
                            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                            await page.wait_for_timeout(2000)
                            await close_all_modals(page)
                            gc.collect()
                            print(f"  [MEMORY] Page refreshed and GC completed")
                        except Exception as refresh_err:
                            print(f"  [WARNING] Page refresh failed: {refresh_err}")
                    
                    # Wait between bets
                    if i < len(bet_slips) - 1:
                        wait_success = await wait_between_bets(page, seconds=5, add_random=True)
                        
                        # If wait was interrupted, just log it (no restart)
                        if not wait_success:
                            print("\n[WARNING] Wait interrupted - continuing anyway...")
                
                elif success == "RETRY":
                    # Click failed but may succeed on retry - don't count as failed yet
                    print(f"\n⚠️ [RETRY NEEDED] Bet slip {bet_slip['slip_number']} click failed - will retry...")
                    
                    # Wait a bit before retrying
                    print("  Waiting 10 seconds before retry...")
                    await page.wait_for_timeout(10000)
                    
                    # Retry the same bet
                    print(f"\n🔄 RETRYING BET {bet_slip['slip_number']}/{len(bet_slips)}...")
                    retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                    
                    if retry_success == True:
                        successful += 1
                        print(f"\n[SUCCESS] Retry bet slip {bet_slip['slip_number']} placed!")
                        
                        # Save progress
                        with open(progress_file, 'w') as f:
                            json.dump({
                                'last_completed_bet': i + 1,
                                'last_successful_bet': i,
                                'successful': successful,
                                'failed': failed,
                                'match_fingerprint': current_match_fingerprint,
                                'timestamp': datetime.now().isoformat(),
                                'matches_data': matches  # Save match data for resume
                            }, f)
                        
                        # Wait between bets
                        if i < len(bet_slips) - 1:
                            await wait_between_bets(page, seconds=5, add_random=True)
                    else:
                        # Retry also failed - now count as failed
                        failed += 1
                        failed_bets.append(bet_slip)
                        print(f"\n❌ [FAILED] Retry bet slip {bet_slip['slip_number']} also failed!")
                        
                        # Save progress at failed bet (so resume will retry THIS bet)
                        with open(progress_file, 'w') as f:
                            json.dump({
                                'last_completed_bet': i,  # Resume FROM this bet
                                'last_successful_bet': i - 1 if i > 0 else -1,
                                'successful': successful,
                                'failed': failed,
                                'match_fingerprint': current_match_fingerprint,
                                'timestamp': datetime.now().isoformat(),
                                'matches_data': matches  # Save match data for resume
                            }, f)
                        
                        print(f"\n{'='*60}")
                        print(f"⛔ TERMINATING - BET FAILED AFTER RETRY")
                        print(f"{'='*60}")
                        print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                        print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                        print(f"📋 Progress saved - run again to resume from bet {bet_slip['slip_number']}")
                        print(f"{'='*60}\n")
                        
                        try:
                            await page.close()
                            await browser.close()
                        except:
                            pass
                        
                        import sys
                        sys.exit(1)
                
                elif success == "RELOGIN":
                    # Session expired during bet - need to re-login and retry
                    print(f"\n🔄 [RE-LOGIN REQUIRED] Session expired during bet {bet_slip['slip_number']}")
                    print("  Attempting to re-authenticate...")
                    
                    # Attempt re-login
                    relogin_success = await check_and_relogin(page, browser)
                    
                    if relogin_success:
                        print("  ✅ Re-login successful! Verifying login state...")
                        
                        # CRITICAL: Verify login was actually successful by checking for balance
                        await page.wait_for_timeout(2000)
                        balance_verified = False
                        for verify_attempt in range(3):
                            try:
                                balance_elem = await page.query_selector('strong:has-text("Balance")')
                                if balance_elem and await balance_elem.is_visible():
                                    parent = await balance_elem.evaluate_handle('el => el.closest("div")')
                                    balance_text = await parent.inner_text()
                                    balance_clean = balance_text.replace('\n', ' ').strip()
                                    print(f"  ✓ Login verified: {balance_clean}")
                                    balance_verified = True
                                    break
                            except:
                                pass
                            
                            if verify_attempt < 2:
                                print(f"  ⏳ Verifying login... (attempt {verify_attempt + 1}/3)")
                                await page.wait_for_timeout(1500)
                        
                        if not balance_verified:
                            print("  ⚠️ Could not verify login, but continuing anyway...")
                        
                        # Navigate to clear betslip completely before retrying
                        print("  Clearing betslip before retry...")
                        try:
                            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                            await page.wait_for_timeout(3000)  # Increased wait time
                            await close_all_modals(page)
                            
                            # Verify betslip is empty by scrolling to it
                            await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                            await page.wait_for_timeout(1000)
                            print("  ✓ Betslip cleared and page ready")
                        except Exception as nav_err:
                            print(f"  ⚠️ Navigation warning: {nav_err}")
                        
                        await page.wait_for_timeout(1000)
                        
                        # Retry the bet after re-login
                        retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                        
                        if retry_success == True:
                            successful += 1
                            print(f"\n[SUCCESS] Bet slip {bet_slip['slip_number']} placed after re-login!")
                            
                            # Save progress
                            with open(progress_file, 'w') as f:
                                json.dump({
                                    'last_completed_bet': i + 1,
                                    'last_successful_bet': i,
                                    'successful': successful,
                                    'failed': failed,
                                    'match_fingerprint': current_match_fingerprint,
                                    'timestamp': datetime.now().isoformat(),
                                    'matches_data': matches
                                }, f)
                            
                            # Wait between bets
                            if i < len(bet_slips) - 1:
                                await wait_between_bets(page, seconds=5, add_random=True)
                        else:
                            # Retry after re-login also failed
                            failed += 1
                            failed_bets.append(bet_slip)
                            print(f"\n❌ [FAILED] Bet slip {bet_slip['slip_number']} failed even after re-login!")
                            
                            # Save progress and terminate
                            with open(progress_file, 'w') as f:
                                json.dump({
                                    'last_completed_bet': i,
                                    'last_successful_bet': i - 1 if i > 0 else -1,
                                    'successful': successful,
                                    'failed': failed,
                                    'match_fingerprint': current_match_fingerprint,
                                    'timestamp': datetime.now().isoformat(),
                                    'matches_data': matches
                                }, f)
                            
                            print(f"\n{'='*60}")
                            print(f"⛔ TERMINATING - BET FAILED AFTER RE-LOGIN")
                            print(f"{'='*60}")
                            print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                            print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                            print(f"📋 Progress saved - run again to resume from bet {bet_slip['slip_number']}")
                            print(f"{'='*60}\n")
                            
                            try:
                                await page.close()
                                await browser.close()
                            except:
                                pass
                            
                            import sys
                            sys.exit(1)
                    else:
                        # Re-login failed - cannot continue
                        failed += 1
                        failed_bets.append(bet_slip)
                        print(f"\n❌ [FATAL] Re-login failed! Cannot continue betting.")
                        
                        # Save progress and terminate
                        with open(progress_file, 'w') as f:
                            json.dump({
                                'last_completed_bet': i,
                                'last_successful_bet': i - 1 if i > 0 else -1,
                                'successful': successful,
                                'failed': failed,
                                'match_fingerprint': current_match_fingerprint,
                                'timestamp': datetime.now().isoformat(),
                                'matches_data': matches
                            }, f)
                        
                        print(f"\n{'='*60}")
                        print(f"⛔ TERMINATING - RE-LOGIN FAILED")
                        print(f"{'='*60}")
                        print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                        print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                        print(f"📋 Progress saved - run again to resume from bet {bet_slip['slip_number']}")
                        print(f"{'='*60}\n")
                        
                        try:
                            await page.close()
                            await browser.close()
                        except:
                            pass
                        
                        import sys
                        sys.exit(1)
                
                else:
                    failed += 1
                    failed_bets.append(bet_slip)  # Store failed bet for retry
                    print(f"\n❌ [FAILED] Bet slip {bet_slip['slip_number']} failed!")
                    
                    # Save progress at failed bet (so resume will retry THIS bet)
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i,  # Resume FROM this bet, not next
                            'last_successful_bet': i - 1 if i > 0 else -1,
                            'successful': successful,
                            'failed': failed,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat(),
                            'matches_data': matches  # Save match data for resume
                        }, f)
                    
                    # CRITICAL: Terminate ALL bets if any bet fails
                    print(f"\n{'='*60}")
                    print(f"⛔ TERMINATING ALL BETS - BET FAILED")
                    print(f"{'='*60}")
                    print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                    print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                    print(f"📋 Progress saved - run again to resume from bet {bet_slip['slip_number']}")
                    print(f"{'='*60}\n")
                    
                    # Close browser and exit application completely
                    try:
                        await page.close()
                        await browser.close()
                    except:
                        pass
                    
                    import sys
                    sys.exit(1)
                
                await asyncio.sleep(2)
                
            except Exception as e:
                error_str = str(e)
                
                # Check if this is a Playwright memory/object collection error
                is_memory_error = "'dict' object has no attribute '_object'" in error_str or \
                                  "object has been collected" in error_str or \
                                  "unbounded heap growth" in error_str or \
                                  "Target page, context or browser has been closed" in error_str
                
                if is_memory_error:
                    print(f"\n{'='*60}")
                    print(f"⚠️ PLAYWRIGHT MEMORY ERROR DETECTED")
                    print(f"{'='*60}")
                    print(f"Error: {error_str[:150]}...")
                    print(f"\n🔄 Attempting recovery with fresh browser...")
                    
                    # Force garbage collection
                    gc.collect()
                    
                    # Try to restart browser and recover
                    try:
                        restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                        if restart_result:
                            page = restart_result["page"]
                            browser = restart_result["browser"]
                            
                            # Clear and rebuild cache
                            outcome_button_cache.clear()
                            print(f"  [RECOVERY] Re-caching outcome buttons...")
                            for match_idx, match in enumerate(matches[:num_matches], 1):
                                match_url = match.get('url')
                                if match_url:
                                    try:
                                        await page.goto(match_url, wait_until='domcontentloaded', timeout=15000)
                                        await page.wait_for_timeout(1000)
                                        await close_all_modals(page)
                                        for selector in ['div.grid.p-1 > div.flex.items-center.justify-between.h-12', 'div[price]']:
                                            buttons = await page.query_selector_all(selector)
                                            if len(buttons) >= 3:
                                                outcome_button_cache[match_url] = selector
                                                break
                                    except:
                                        pass
                            
                            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                            await close_all_modals(page)
                            
                            print(f"\n✅ RECOVERY SUCCESSFUL - Retrying bet {bet_slip['slip_number']}...")
                            
                            # Retry the failed bet with fresh browser
                            retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                            
                            if retry_success == True:
                                successful += 1
                                print(f"\n[SUCCESS] Bet slip {bet_slip['slip_number']} placed after browser restart!")
                                
                                # Save progress
                                with open(progress_file, 'w') as f:
                                    json.dump({
                                        'last_completed_bet': i + 1,
                                        'last_successful_bet': i,
                                        'successful': successful,
                                        'failed': failed,
                                        'match_fingerprint': current_match_fingerprint,
                                        'timestamp': datetime.now().isoformat(),
                                        'matches_data': matches
                                    }, f)
                                
                                # Continue to next bet
                                if i < len(bet_slips) - 1:
                                    await wait_between_bets(page, seconds=5, add_random=True)
                                continue  # Skip to next iteration of the loop
                            else:
                                print(f"\n❌ Retry after browser restart also failed")
                                # Fall through to exit
                        else:
                            print(f"\n❌ Browser restart failed")
                    except Exception as restart_err:
                        print(f"\n❌ Recovery failed: {restart_err}")
                    
                    # If we get here, recovery failed - save progress and exit
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i,  # Resume FROM this bet
                            'last_successful_bet': i - 1 if i > 0 else -1,
                            'successful': successful,
                            'failed': failed,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat(),
                            'matches_data': matches
                        }, f)
                    
                    print(f"\n{'='*60}")
                    print(f"⚠️ PLAYWRIGHT MEMORY ERROR - RESTART REQUIRED")
                    print(f"{'='*60}")
                    print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                    print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                    print(f"📋 Progress saved - auto-restart will resume")
                    print(f"{'='*60}\n")
                    
                    try:
                        await page.close()
                        await browser.close()
                    except:
                        pass
                    
                    import sys
                    sys.exit(1)
                
                # Regular exception handling
                failed += 1
                failed_bets.append(bet_slip)  # Store failed bet for retry
                print(f"\n[ERROR] Exception on slip {bet_slip['slip_number']}: {e}")
                
                # Save progress at failed bet (so resume will retry THIS bet)
                with open(progress_file, 'w') as f:
                    json.dump({
                        'last_completed_bet': i,  # Resume FROM this bet, not next
                        'last_successful_bet': i - 1 if i > 0 else -1,
                        'successful': successful,
                        'failed': failed,
                        'match_fingerprint': current_match_fingerprint,
                        'timestamp': datetime.now().isoformat(),
                        'matches_data': matches  # Save match data for resume
                    }, f)
                
                # CRITICAL: Terminate ALL bets after exception
                print(f"\n{'='*60}")
                print(f"⛔ TERMINATING ALL BETS - EXCEPTION OCCURRED")
                print(f"{'='*60}")
                print(f"Error: {str(e)}")
                print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                print(f"📋 Progress saved - run again to resume from bet {bet_slip['slip_number']}")
                print(f"{'='*60}\n")
                
                # Close browser and exit application completely
                try:
                    await page.close()
                    await browser.close()
                except:
                    pass
                
                import sys
                sys.exit(1)
        
        # Retry failed bets
        if len(failed_bets) > 0:
            print("\n" + "="*60)
            print(f"RETRYING {len(failed_bets)} FAILED BET(S)")
            print("="*60)
            
            for retry_bet in failed_bets:
                print(f"\n{'='*60}")
                print(f"RETRY: BET {retry_bet['slip_number']}/{len(bet_slips)}")
                print(f"{'='*60}")
                
                try:
                    success = await place_bet_slip(page, retry_bet, amount_per_slip, match_cache, outcome_button_cache)
                    
                    if success:
                        successful += 1
                        failed -= 1
                        print(f"\n[SUCCESS] Retry bet slip {retry_bet['slip_number']} placed!")
                        
                        # Wait between bets
                        wait_success = await wait_between_bets(page, seconds=5, add_random=True)
                        
                        # If wait was interrupted, just log it (no restart)
                        if not wait_success:
                            print("\n[WARNING] Wait interrupted during retry - continuing anyway...")
                    else:
                        print(f"\n[FAILED] Retry bet slip {retry_bet['slip_number']} failed again!")
                    
                    await asyncio.sleep(2)
                    
                except Exception as e:
                    print(f"\n[ERROR] Exception on retry slip {retry_bet['slip_number']}: {e}")
        
        print("\n" + "="*60)
        print(f"FINAL RESULTS: {successful}/{len(bet_slips)} successful, {failed} failed")
        print(f"Total amount wagered: R{successful * amount_per_slip:.2f}")
        print("="*60)
        
        # Clean up progress file ONLY if ALL bets successful
        if os.path.exists(progress_file):
            if failed == 0 and successful == len(bet_slips):
                try:
                    os.remove(progress_file)
                    print("\n✅ [CLEANUP] Progress file removed - all bets completed successfully!")
                except:
                    pass
            else:
                print(f"\n📋 [INFO] Progress file kept - {failed} failed bet(s)")
                print(f"[INFO] Re-run script to resume from bet {successful + 1}")
        
        print("\nKeeping browser open for 30 seconds...")
        await page.wait_for_timeout(30000)
        
        await browser.close()
        
        # End timer and display results
        script_end_time = time.time()
        total_duration = script_end_time - script_start_time
        hours = int(total_duration // 3600)
        minutes = int((total_duration % 3600) // 60)
        seconds = int(total_duration % 60)
        
        print("\n" + "="*60)
        print("⏱️  SCRIPT EXECUTION TIME")
        print("="*60)
        if hours > 0:
            print(f"Total time: {hours}h {minutes}m {seconds}s ({total_duration:.2f} seconds)")
        elif minutes > 0:
            print(f"Total time: {minutes}m {seconds}s ({total_duration:.2f} seconds)")
        else:
            print(f"Total time: {seconds}s ({total_duration:.2f} seconds)")
        print("="*60)

def main():
    """Main function to run the Betway automation
    
    Usage:
        Interactive Mode: python main.py
        CLI Mode: python main.py <num_matches> <amount_per_slip>
        Example (test with 1 match): python main.py 1 1.0
        Example (2 matches): python main.py 2 1.0
    """
    import sys
    
    # Check for command-line arguments (for automated testing)
    num_matches = None
    amount_per_slip = None
    
    if len(sys.argv) >= 3:
        try:
            num_matches = int(sys.argv[1])
            amount_per_slip = float(sys.argv[2])
            
            # Validate num_matches
            if num_matches < 1:
                print("[ERROR] Number of matches must be at least 1")
                print("Usage: python main.py <num_matches> <amount_per_slip>")
                print("Example: python main.py 1 1.0")
                return
            
            # Validate amount
            if amount_per_slip <= 0:
                print("[ERROR] Amount per slip must be greater than 0")
                print("Usage: python main.py <num_matches> <amount_per_slip>")
                print("Example: python main.py 2 1.0")
                return
            
            # Warn about large number of matches
            total_bets = 3 ** num_matches
            if num_matches > 5:
                print(f"[WARNING] {num_matches} matches will generate {total_bets:,} bets!")
                print(f"[WARNING] This may take a very long time and cost R{total_bets * amount_per_slip:,.2f}")
            
            print(f"[CLI MODE] Using arguments: {num_matches} matches, R{amount_per_slip} per slip")
            print(f"[CLI MODE] Total bets: {total_bets}, Total cost: R{total_bets * amount_per_slip:.2f}")
        except ValueError:
            print("Usage: python main.py <num_matches> <amount_per_slip>")
            print("Example (test): python main.py 1 1.0")
            print("Example: python main.py 2 1.0")
            return
    
    asyncio.run(main_async(num_matches=num_matches, amount_per_slip=amount_per_slip))


def main_with_auto_retry():
    """
    Wrapper that automatically restarts the script as a NEW PROCESS when it crashes.
    This is the correct solution for Playwright memory corruption errors because:
    1. A fresh process has clean memory state
    2. Progress is saved to file, so new process resumes from where it left off
    3. Uses subprocess to spawn completely isolated Python process
    
    IMPORTANT: This wrapper catches ALL crashes including:
    - Playwright memory errors (exit code 1)
    - Unexpected exceptions (any non-zero exit code)
    - Process timeouts (hangs)
    - Signal terminations
    """
    import sys
    import subprocess
    import time as time_module
    import signal
    
    MAX_RETRIES = 5  # Maximum number of automatic restarts
    RETRY_DELAY = 15  # Seconds to wait before restarting
    SUBPROCESS_TIMEOUT = 3600  # 1 hour timeout per subprocess (prevents hangs)
    
    # Get the original arguments
    args = sys.argv[1:]  # Everything after script name
    
    if len(args) < 2:
        # No CLI args - run interactively (no auto-retry for interactive mode)
        main()
        return
    
    total_start_time = time_module.time()
    retry_count = 0
    
    print(f"\n{'='*60}")
    print(f"🚀 AUTO-RETRY WRAPPER ACTIVE")
    print(f"{'='*60}")
    print(f"   Max retries: {MAX_RETRIES}")
    print(f"   Retry delay: {RETRY_DELAY}s")
    print(f"   Subprocess timeout: {SUBPROCESS_TIMEOUT}s ({SUBPROCESS_TIMEOUT//60} min)")
    print(f"   Progress file: bet_progress.json")
    print(f"{'='*60}\n")
    
    while retry_count <= MAX_RETRIES:
        attempt_start_time = time_module.time()
        
        print(f"\n{'='*60}")
        if retry_count > 0:
            elapsed_total = time_module.time() - total_start_time
            elapsed_mins = int(elapsed_total // 60)
            elapsed_secs = int(elapsed_total % 60)
            print(f"🔄 AUTO-RESTART ATTEMPT {retry_count}/{MAX_RETRIES}")
            print(f"   Total elapsed time: {elapsed_mins}m {elapsed_secs}s")
            print(f"   Spawning fresh Python process...")
            print(f"   Will resume from last saved progress...")
        else:
            print(f"🚀 STARTING BETWAY AUTOMATION (Attempt 1)")
        print(f"{'='*60}\n")
        
        # Build command to run main() directly (not main_with_auto_retry)
        # We use a special --direct flag to indicate this
        cmd = [sys.executable, sys.argv[0], '--direct'] + args
        
        exit_code = None
        error_reason = None
        
        try:
            # Run as subprocess - this is a COMPLETELY NEW Python process
            # Use timeout to prevent infinite hangs
            result = subprocess.run(
                cmd,
                cwd=os.getcwd(),
                timeout=SUBPROCESS_TIMEOUT,
                # Don't capture output - let it print directly to console
            )
            
            exit_code = result.returncode
            
        except subprocess.TimeoutExpired:
            # Process hung - need to restart
            exit_code = -1
            error_reason = f"TIMEOUT (hung for >{SUBPROCESS_TIMEOUT}s)"
            print(f"\n⚠️ SUBPROCESS TIMEOUT - Process hung for over {SUBPROCESS_TIMEOUT} seconds")
            
        except KeyboardInterrupt:
            elapsed_total = time_module.time() - total_start_time
            elapsed_mins = int(elapsed_total // 60)
            print(f"\n\n⛔ Interrupted by user (Ctrl+C)")
            print(f"   Total time: {elapsed_mins} minutes")
            print(f"   Progress saved. Run again to resume.")
            return  # Exit completely
            
        except Exception as e:
            exit_code = -2
            error_reason = f"SUBPROCESS ERROR: {str(e)[:100]}"
            print(f"\n❌ Subprocess error: {e}")
        
        # Check result
        if exit_code == 0:
            # Success!
            elapsed_total = time_module.time() - total_start_time
            elapsed_mins = int(elapsed_total // 60)
            elapsed_secs = int(elapsed_total % 60)
            print(f"\n{'='*60}")
            print(f"✅ SCRIPT COMPLETED SUCCESSFULLY!")
            print(f"   Total time: {elapsed_mins}m {elapsed_secs}s")
            print(f"   Retries used: {retry_count}")
            print(f"{'='*60}\n")
            return  # Exit successfully
        
        # Script crashed or errored - need to restart
        retry_count += 1
        attempt_elapsed = time_module.time() - attempt_start_time
        
        if retry_count <= MAX_RETRIES:
            print(f"\n{'='*60}")
            if error_reason:
                print(f"⚠️ SCRIPT FAILED: {error_reason}")
            else:
                print(f"⚠️ SCRIPT CRASHED (exit code: {exit_code})")
            print(f"   Attempt ran for: {int(attempt_elapsed)}s")
            print(f"   Progress is saved to bet_progress.json")
            print(f"\n🔄 AUTO-RESTART in {RETRY_DELAY} seconds...")
            print(f"   Restart attempt: {retry_count}/{MAX_RETRIES}")
            print(f"{'='*60}\n")
            
            # Force garbage collection in parent process too
            gc.collect()
            
            time_module.sleep(RETRY_DELAY)
        else:
            elapsed_total = time_module.time() - total_start_time
            elapsed_mins = int(elapsed_total // 60)
            print(f"\n{'='*60}")
            print(f"❌ MAX RETRIES REACHED ({MAX_RETRIES})")
            print(f"   Total time spent: {elapsed_mins} minutes")
            print(f"\n💡 Progress is saved. Restart manually with:")
            print(f"   python main.py {' '.join(args)}")
            print(f"{'='*60}\n")
            return  # Exit after max retries


if __name__ == "__main__":
    import sys
    
    # Check if we're being called directly (by subprocess) or as the main entry point
    if len(sys.argv) >= 2 and sys.argv[1] == '--direct':
        # Called by subprocess - run main() directly
        # Remove the --direct flag and pass remaining args
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        main()
    else:
        # Called normally - use the auto-retry wrapper
        main_with_auto_retry()
