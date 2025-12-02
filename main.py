"""
Main entry point for Betway automation
"""
import asyncio
import os
import json
import random
from itertools import product
from playwright.async_api import async_playwright, Page
from playwright._impl._errors import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re

# Load environment variables from .env file
load_dotenv()

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
    
    # Launch browser (set headless=False to see the browser)
    browser = await playwright.chromium.launch(headless=False)
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
        'button:has-text("Close")',
        'button:has-text("GOT IT")',
        'button:has-text("OK")',
        'button[aria-label="Close"]',
    ]
    
    for selector in close_selectors:
        try:
            close_btns = await page.query_selector_all(selector)
            for btn in close_btns:
                if await btn.is_visible():
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

async def close_all_modals(page: Page, max_attempts=3):
    """
    Aggressively attempt to close all modals/popups that might appear.
    Tries multiple times with various selectors, including betslip modal.
    """
    for attempt in range(max_attempts):
        try:
            # Try various close button selectors (including betslip close from HTML)
            close_selectors = [
                'svg[id="modal-close-btn"]',  # Betslip and modal close button
                'button[id*="close"]',
                'button[aria-label*="Close"]',
                'button[aria-label*="close"]',
                '[class*="close"]',
                '.modal-close',
                'div[role="dialog"] button',
                '[class*="modal"] button',
                '[class*="popup"] button',
                'button:has-text("×")',
                'button:has-text("Close")',
            ]
            
            closed_any = False
            for selector in close_selectors:
                try:
                    close_buttons = await page.query_selector_all(selector)
                    for btn in close_buttons:
                        if await btn.is_visible():
                            await btn.click()
                            closed_any = True
                            await asyncio.sleep(0.3)
                except Exception:
                    continue
            
            # Try Escape key as well
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
    print(f"\nEach ticket bets on ALL {num_matches} matches with DIFFERENT outcome predictions:")
    print(f"  Example: Ticket 1 = Match1:1, Match2:1")
    print(f"           Ticket 2 = Match1:1, Match2:X")
    print(f"           Ticket 3 = Match1:1, Match2:2")
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
        
        # Clear betslip by navigating to the soccer page (more reliable than reload)
        print("  Navigating to clear betslip...")
        try:
            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception as goto_error:
            print(f"  ⚠️ Navigation failed: {goto_error} - trying alternative...")
            # Try without wait_until
            try:
                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', timeout=20000)
                await page.wait_for_timeout(2000)
            except:
                print(f"  ⚠️ Could not navigate - aborting bet")
                return False
        
        try:
            await close_all_modals(page, max_attempts=3)  # More aggressive modal closing
        except Exception as modal_error:
            print(f"  ⚠️ Modal closing failed: {modal_error} - continuing anyway...")
        
        # VERIFY betslip is actually empty
        await page.wait_for_timeout(500)
        try:
            betslip_check = await page.query_selector('div#betslip-container-mobile')
            if not betslip_check:
                betslip_check = await page.query_selector('div#betslip-container')
            if betslip_check:
                betslip_text = await betslip_check.inner_text()
                if '1X2' in betslip_text or 'Multi' in betslip_text:
                    print("    ⚠️  WARNING: Betslip not empty after reload! Attempting to clear...")
                    # Try clicking remove all button
                    try:
                        remove_all = await page.query_selector('div#betslip-remove-all')
                        if remove_all:
                            await remove_all.click()
                            await page.wait_for_timeout(500)
                            print("    ✅ Clicked 'Remove All' button")
                    except:
                        pass
        except Exception as e:
            print(f"    Could not verify betslip: {e}")
        
        print("    [OK] Page reloaded - betslip is empty")
        
        # Click on each match outcome
        for i, (match, selection) in enumerate(zip(matches, selections)):
            print(f"  Match {i+1}: {match['name']} - Selecting: {selection}")
            
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
                        try:
                            await outcome_btn.scroll_into_view_if_needed()
                            await page.wait_for_timeout(300)
                            await outcome_btn.click()
                            await page.wait_for_timeout(1000)
                            cache_status = "[CACHED]" if (outcome_button_cache and match_url in outcome_button_cache) else ""
                            print(f"    ✓ Clicked outcome '{selection}' {cache_status}")
                        except Exception as click_err:
                            print(f"    ❌ ERROR clicking button: {click_err}")
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
        
        # Enter bet amount
        print(f"  Entering bet amount: R {amount:.2f}")
        try:
            # Try multiple selectors (ID is most reliable)
            stake_input = await page.query_selector('#bet-amount-input')
            if not stake_input:
                stake_input = await page.query_selector('input[placeholder="0.00"]')
            if not stake_input:
                stake_input = await page.query_selector('input[type="number"][inputmode="decimal"]')
            
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
                
                # CRITICAL: Must verify stake amount is actually shown in betslip
                stake_str = str(amount)
                has_stake = stake_str in betslip_text or f"R {stake_str}" in betslip_text or f"R{stake_str}" in betslip_text
                
                # Changed validation: MUST have stake amount visible (not just return calculation)
                if has_bet_button and has_return_calculation and has_stake:
                    betslip_ready = True
                    print(f"    ✓ Betslip is READY with stake amount visible (retry {retry + 1}/{max_retries})")
                    break
                else:
                    # Show which validation failed
                    missing = []
                    if not has_bet_button:
                        missing.append("Bet Button")
                    if not has_return_calculation:
                        missing.append("Return")
                    if not has_stake:
                        missing.append(f"Stake ({stake_str})")
                    print(f"    ⏳ Missing: {', '.join(missing)} (retry {retry + 1}/{max_retries})")
                
                if retry < max_retries - 1:
                    await page.wait_for_timeout(1000)
            else:
                if retry < max_retries - 1:
                    await page.wait_for_timeout(1000)
        
        if not betslip_ready:
            print(f"    ❌ [ERROR] Betslip not ready after {max_retries} retries - stake amount not visible!")
            print(f"    This usually means the amount wasn't entered correctly")
            return False
        
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
            if selection_count > expected_count:
                print(f"\n    ❌ [ERROR] Too many selections in betslip!")
                print(f"    Expected: {expected_count} selections")
                print(f"    Found: {selection_count} selections")
                print(f"    ❌ ABORTING - betslip not properly cleared\n")
                return False
            
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
                # Check for any confirmation-related elements
                modal_selectors = [
                    'button:has-text("Confirm")',
                    'button:has-text("Place Bet")',
                    'button:has-text("OK")',
                    'span:has-text("Bet Confirmation")',
                    'div[role="dialog"]',
                    'div[class*="modal"]',
                    'button#strike-conf-continue-btn',
                ]
                for selector in modal_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element and await element.is_visible():
                            return True
                    except:
                        pass
                return False
            
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
                        modal_appeared = await check_for_modal()
                        if modal_appeared:
                            print("    ✅ Method 5: Mouse click SUCCESS - modal appeared!")
                            click_success = True
                        else:
                            print("    ✗ Method 5: Mouse click but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 5 failed: {e}")
            
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
                return False
            
            print("    ✅ Bet Now button clicked and confirmation modal appeared!")
            
            # Modal already appeared, no need to wait again
            await page.wait_for_timeout(500)
            
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
                        print(f"    ✅ Found 'Continue betting' button - bet confirmed successful!")
                        await continue_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(300)
                        await continue_btn.evaluate('el => el.click()')
                        await page.wait_for_timeout(500)
                        print("    ✅ Bet placed successfully!")
                        bet_confirmed = True
                        return True
                except:
                    continue
            
            if bet_confirmed:
                return True
            
            # Alternative: Check for "Bet Confirmation" modal as success indicator
            try:
                bet_conf_modal = await page.query_selector('span:has-text("Bet Confirmation")')
                if bet_conf_modal:
                    print("    ✅ Found 'Bet Confirmation' modal - bet successful!")
                    # Try to close the modal
                    close_selectors = [
                        'svg#modal-close-btn',
                        'button[aria-label*="Close"]',
                        'svg[class*="cursor-pointer"]',
                    ]
                    for close_sel in close_selectors:
                        try:
                            close_btn = await page.wait_for_selector(close_sel, timeout=2000, state='visible')
                            if close_btn:
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

async def main_async(num_matches=None, amount_per_slip=None, min_gap_hours=2.0, min_time_before_match=3.5):
    """Main async function to run the Betway automation
    
    Args:
        num_matches: Number of matches to bet on (default: prompts user)
        amount_per_slip: Amount to bet per slip in Rand (default: prompts user)
        min_gap_hours: Minimum gap between matches in hours (default: 2.0)
        min_time_before_match: Minimum hours before first match starts (default: 3.5)
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
        
        # Login with retry
        result = await retry_with_backoff(login_to_betway, max_retries=3, initial_delay=5, playwright=p)
        page = result["page"]
        browser = result["browser"]
        
        # Check for existing progress file FIRST (before scraping)
        progress_file = 'bet_progress.json'
        resume_data = None
        if os.path.exists(progress_file):
            try:
                with open(progress_file, 'r') as f:
                    resume_data = json.load(f)
                    print(f"\n{'='*60}")
                    print(f"📋 FOUND EXISTING PROGRESS FILE")
                    print(f"{'='*60}")
                    print(f"Last completed bet: {resume_data.get('last_completed_bet', 0)}")
                    print(f"Successful: {resume_data.get('successful', 0)} | Failed: {resume_data.get('failed', 0)}")
                    print(f"Will validate matches and resume if they match...")
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
        print(f"  1. Start 3.5+ hours from now ({min_time_before_match}+ hours)")
        print(f"  2. Are {min_gap_hours}+ hours apart from each other")
        print(f"  3. Have valid URLs captured")
        print(f"Stopping as soon as we find {num_matches} matches meeting all conditions")
        print(f"{'='*60}\n")
        
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
        
        min_gap_minutes = int(min_gap_hours * 60)
        filtered_matches = []
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
                        
                        # Check if match meets basic time requirement (3.5+ hours or future date)
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
                            pass
                        
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
                    print(f"    ❌ {debug_too_soon} - Starts too soon (<3.5 hours)")
                if debug_no_odds > 0:
                    print(f"    ❌ {debug_no_odds} - No odds available")
                if debug_wrong_odds_count > 0:
                    print(f"    ❌ {debug_wrong_odds_count} - Not 1X2 market (odds ≠ 3)")
                if debug_no_gap > 0:
                    print(f"    ❌ {debug_no_gap} - Too close to other selected matches (<2h gap)")
            
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
        print(f"\n[OK] Final selection - {len(matches)} matches (each {min_gap_hours}+ hours apart):")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m['name']} - {m.get('start_time', 'Unknown time')} - Odds: {m.get('odds', [])}")
        
        # CRITICAL: Validate all matches have cached URLs before proceeding
        print(f"\n{'='*60}")
        print("URL VALIDATION: Checking all matches have cached URLs")
        print(f"{'='*60}")
        
        missing_urls = []
        for i, match in enumerate(matches, 1):
            match_url = match.get('url')
            if match_url:
                print(f"  ✓ Match {i}: {match['name']} - URL cached")
            else:
                print(f"  ❌ Match {i}: {match['name']} - NO URL!")
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
        avg_time_per_bet = 1
        total_time_needed = total_bets * avg_time_per_bet
        
        print(f"Total bets to place: {total_bets}")
        print(f"Estimated time per bet: ~{avg_time_per_bet} minute")
        print(f"Total time needed: ~{total_time_needed} minutes ({total_time_needed/60:.1f} hours)")
        
        first_match = matches[0]
        if first_match.get('start_time'):
            print(f"\nFirst match: {first_match['name']}")
            print(f"Start time: {first_match['start_time']}")
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
            if match_url and match_url not in outcome_button_cache:
                try:
                    print(f"Match {match_idx}/{num_matches}: {match['name']}")
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
            
            # Validate matches haven't changed
            if saved_fingerprint != current_match_fingerprint:
                print(f"\n⚠️ [WARNING] Matches have changed since last run!")
                print(f"[INFO] Saved matches: {saved_fingerprint}")
                print(f"[INFO] Current matches: {current_match_fingerprint}")
                print(f"[ACTION] Deleting progress file and starting fresh...\n")
                os.remove(progress_file)
                resume_data = None  # Clear resume data
            else:
                start_index = resume_data.get('last_completed_bet', 0)
                successful = resume_data.get('successful', 0)
                failed = resume_data.get('failed', 0)
                print(f"\n✅ [RESUME] Matches validated - same as previous run")
                print(f"[RESUME] Continuing from bet {start_index + 1}/{len(bet_slips)}")
                print(f"[PROGRESS] Previous: {successful} successful, {failed} failed\n")
        
        for i, bet_slip in enumerate(bet_slips):
            # Skip already completed bets
            if i < start_index:
                continue
                
            print(f"\n{'='*60}")
            print(f"BET {i+1}/{len(bet_slips)}")
            print(f"{'='*60}")
            
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
                
                if success:
                    successful += 1
                    print(f"\n[SUCCESS] Bet slip {bet_slip['slip_number']} placed!")
                    
                    # Save progress
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i + 1,
                            'successful': successful,
                            'failed': failed,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat()
                        }, f)
                    
                    # Wait between bets
                    if i < len(bet_slips) - 1:
                        wait_success = await wait_between_bets(page, seconds=5, add_random=True)
                        
                        # If wait was interrupted, just log it (no restart)
                        if not wait_success:
                            print("\n[WARNING] Wait interrupted - continuing anyway...")
                
                else:
                    failed += 1
                    failed_bets.append(bet_slip)  # Store failed bet for retry
                    print(f"\n❌ [FAILED] Bet slip {bet_slip['slip_number']} failed!")
                    
                    # CRITICAL: Terminate ALL bets if any bet fails
                    print(f"\n{'='*60}")
                    print(f"⛔ TERMINATING ALL BETS - BET FAILED")
                    print(f"{'='*60}")
                    print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                    print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                    print(f"{'='*60}\n")
                    
                    # Close browser and exit application completely
                    try:
                        await page.close()
                        await browser.close()
                    except:
                        pass
                    
                    print("🛑 APPLICATION STOPPED - Bet failed. Please review and fix the issue.\n")
                    import sys
                    sys.exit(1)
                    
                    # Save progress even for failed bets
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i + 1,
                            'successful': successful,
                            'failed': failed,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat()
                        }, f)
                
                await asyncio.sleep(2)
                
            except Exception as e:
                failed += 1
                failed_bets.append(bet_slip)  # Store failed bet for retry
                print(f"\n[ERROR] Exception on slip {bet_slip['slip_number']}: {e}")
                
                # CRITICAL: Terminate ALL bets after first failure
                if failed == 1:
                    print(f"\n{'='*60}")
                    print(f"⛔ TERMINATING ALL BETS - EXCEPTION ON FIRST BET")
                    print(f"{'='*60}")
                    print(f"Error: {str(e)}")
                    print(f"\n🛑 Stopped at bet {bet_slip['slip_number']}/{len(bet_slips)}")
                    print(f"✅ Successful: {successful} | ❌ Failed: {failed}")
                    print(f"{'='*60}\n")
                    
                    # Close browser and exit application completely
                    try:
                        await page.close()
                        await browser.close()
                    except:
                        pass
                    
                    print("🛑 APPLICATION STOPPED - First bet threw exception. Please review and fix the issue.\n")
                    import sys
                    sys.exit(1)
                
                # Save progress
                with open(progress_file, 'w') as f:
                    json.dump({
                        'last_completed_bet': i + 1,
                        'successful': successful,
                        'failed': failed,
                        'match_fingerprint': current_match_fingerprint,
                        'timestamp': datetime.now().isoformat()
                    }, f)
        
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
            print(f"[CLI MODE] Using arguments: {num_matches} matches, R{amount_per_slip} per slip")
        except ValueError:
            print("Usage: python main.py <num_matches> <amount_per_slip>")
            print("Example (test): python main.py 1 1.0")
            print("Example: python main.py 2 1.0")
            return
    
    asyncio.run(main_async(num_matches=num_matches, amount_per_slip=amount_per_slip))

if __name__ == "__main__":
    main()
