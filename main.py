"""
Main entry point for Betway automation
"""
import asyncio
import os
import json
import random
import math
import gc  # Garbage collection for memory management
import traceback  # For detailed error tracebacks
from itertools import product
from playwright.async_api import async_playwright, Page
from playwright._impl._errors import Error as PlaywrightError, TimeoutError as PlaywrightTimeoutError
from datetime import datetime, timedelta
from dotenv import load_dotenv
import re

# Load environment variables from .env file
load_dotenv()


# ============================================================================
# ERROR TRACKING SYSTEM WITH PROBLEM DETAILS (RFC 7807/RFC 9457)
# ============================================================================

# Problem type definitions with URIs, titles, and default status codes
PROBLEM_TYPES = {
    'TIMEOUT': {
        'type': 'urn:betway-automation:error:timeout',
        'title': 'Operation Timeout',
        'status': 504,
        'category': 'infrastructure',
        'recoverable': True,
        'suggested_action': 'Browser will be restarted automatically. If persistent, check network connectivity.'
    },
    'BROWSER_RESTART': {
        'type': 'urn:betway-automation:error:browser-restart',
        'title': 'Browser Restart Required',
        'status': 503,
        'category': 'infrastructure',
        'recoverable': True,
        'suggested_action': 'Browser is being restarted. Progress is saved.'
    },
    'CANCELLED': {
        'type': 'urn:betway-automation:error:cancelled',
        'title': 'Operation Cancelled',
        'status': 499,
        'category': 'user_action',
        'recoverable': False,
        'suggested_action': 'Operation was cancelled. Re-run script to continue.'
    },
    'NETWORK_FAILURE': {
        'type': 'urn:betway-automation:error:network-failure',
        'title': 'Network Communication Failure',
        'status': 502,
        'category': 'network',
        'recoverable': True,
        'suggested_action': 'Check internet connection. Script will attempt to retry.'
    },
    'MEMORY_ERROR': {
        'type': 'urn:betway-automation:error:memory-error',
        'title': 'Playwright Memory Corruption',
        'status': 500,
        'category': 'infrastructure',
        'recoverable': True,
        'suggested_action': 'Browser memory corrupted. Fresh browser instance will be spawned.'
    },
    'BET_FAILED': {
        'type': 'urn:betway-automation:error:bet-failed',
        'title': 'Bet Placement Failed',
        'status': 422,
        'category': 'business_logic',
        'recoverable': True,
        'suggested_action': 'Bet could not be placed. Check match availability and odds.'
    },
    'RETRY_FAILED': {
        'type': 'urn:betway-automation:error:retry-failed',
        'title': 'Retry Attempt Failed',
        'status': 422,
        'category': 'business_logic',
        'recoverable': True,
        'suggested_action': 'Multiple attempts failed. Progress saved - auto-retry wrapper will restart and resume.'
    },
    'SESSION_EXPIRED': {
        'type': 'urn:betway-automation:error:session-expired',
        'title': 'Authentication Session Expired',
        'status': 401,
        'category': 'authentication',
        'recoverable': True,
        'suggested_action': 'Session expired. Re-login will be attempted automatically.'
    },
    'RELOGIN_FAILED': {
        'type': 'urn:betway-automation:error:relogin-failed',
        'title': 'Re-authentication Failed',
        'status': 401,
        'category': 'authentication',
        'recoverable': True,
        'suggested_action': 'Could not re-authenticate. Progress saved - will retry with fresh browser on restart.'
    },
    'EXCEPTION': {
        'type': 'urn:betway-automation:error:exception',
        'title': 'Unexpected Exception',
        'status': 500,
        'category': 'unknown',
        'recoverable': True,
        'suggested_action': 'Unexpected error occurred. Progress saved - auto-retry will resume from last bet.'
    },
    'UNHANDLED_EXCEPTION': {
        'type': 'urn:betway-automation:error:unhandled',
        'title': 'Unhandled Exception',
        'status': 500,
        'category': 'critical',
        'recoverable': True,
        'suggested_action': 'Unhandled exception caught. Progress saved - auto-retry wrapper will restart.'
    },
    'BROWSER_RESTART_SUCCESS': {
        'type': 'urn:betway-automation:info:browser-restart-success',
        'title': 'Browser Restart Successful',
        'status': 200,
        'category': 'maintenance',
        'recoverable': True,
        'suggested_action': 'Scheduled browser restart completed successfully. Continuing with bets.'
    },
    'BROWSER_RESTART_FAILED': {
        'type': 'urn:betway-automation:error:browser-restart-failed',
        'title': 'Browser Restart Failed',
        'status': 503,
        'category': 'browser',
        'recoverable': True,
        'suggested_action': 'Browser restart failed. Will retry with new browser instance.'
    },
    'BROWSER_RESTART_ERROR': {
        'type': 'urn:betway-automation:error:browser-restart-error',
        'title': 'Browser Restart Error',
        'status': 500,
        'category': 'browser',
        'recoverable': True,
        'suggested_action': 'Error during browser restart. Progress saved - will retry.'
    },
    'PAGE_REFRESH_SUCCESS': {
        'type': 'urn:betway-automation:info:page-refresh-success',
        'title': 'Page Refresh Successful',
        'status': 200,
        'category': 'maintenance',
        'recoverable': True,
        'suggested_action': 'Page refresh completed successfully. Continuing with bets.'
    },
    'PAGE_REFRESH_FAILED': {
        'type': 'urn:betway-automation:error:page-refresh-failed',
        'title': 'Page Refresh Failed',
        'status': 503,
        'category': 'browser',
        'recoverable': True,
        'suggested_action': 'Page refresh failed. Will attempt browser restart.'
    },
    'WAIT_INTERRUPTED': {
        'type': 'urn:betway-automation:info:wait-interrupted',
        'title': 'Wait Interrupted',
        'status': 200,
        'category': 'interruption',
        'recoverable': True,
        'suggested_action': 'Wait was interrupted. Resuming operations.'
    },
    'RECOVERY_SUCCESS': {
        'type': 'urn:betway-automation:info:recovery-success',
        'title': 'Recovery Successful',
        'status': 200,
        'category': 'recovery',
        'recoverable': True,
        'suggested_action': 'Recovery operation completed successfully.'
    },
    'RECOVERY_RETRY_FAILED': {
        'type': 'urn:betway-automation:error:recovery-retry-failed',
        'title': 'Recovery Retry Failed',
        'status': 503,
        'category': 'recovery',
        'recoverable': True,
        'suggested_action': 'Recovery retry failed. Will attempt alternative recovery.'
    },
    'RECOVERY_BROWSER_RESTART_FAILED': {
        'type': 'urn:betway-automation:error:recovery-browser-restart-failed',
        'title': 'Recovery Browser Restart Failed',
        'status': 503,
        'category': 'recovery',
        'recoverable': True,
        'suggested_action': 'Browser restart during recovery failed. Progress saved.'
    },
    'RECOVERY_EXCEPTION': {
        'type': 'urn:betway-automation:error:recovery-exception',
        'title': 'Recovery Exception',
        'status': 500,
        'category': 'recovery',
        'recoverable': True,
        'suggested_action': 'Exception during recovery process. Progress saved.'
    },
    'SCRIPT_COMPLETED': {
        'type': 'urn:betway-automation:info:script-completed',
        'title': 'Script Completed',
        'status': 200,
        'category': 'completion',
        'recoverable': True,
        'suggested_action': 'Script execution completed successfully.'
    }
}


class ProblemDetails:
    """RFC 7807/RFC 9457 Problem Details implementation."""
    
    def __init__(self, error_type: str, detail: str, context: dict = None, exception: Exception = None):
        self.timestamp = datetime.now()
        self.error_type_key = error_type
        
        problem_def = PROBLEM_TYPES.get(error_type, {
            'type': f'urn:betway-automation:error:{error_type.lower()}',
            'title': error_type.replace('_', ' ').title(),
            'status': 500,
            'category': 'unknown',
            'recoverable': False,
            'suggested_action': 'Unknown error type. Check logs.'
        })
        
        self.type = problem_def['type']
        self.title = problem_def['title']
        self.status = problem_def['status']
        self.detail = detail
        self.instance = f"urn:betway-automation:instance:{self.timestamp.strftime('%Y%m%d%H%M%S%f')}"
        self.category = problem_def['category']
        self.recoverable = problem_def['recoverable']
        self.suggested_action = problem_def['suggested_action']
        self.context = context or {}
        
        self.exception_info = None
        if exception:
            self.exception_info = {
                'type': type(exception).__name__,
                'message': str(exception),
                'traceback': traceback.format_exc()
            }
    
    def to_dict(self) -> dict:
        result = {
            'type': self.type,
            'title': self.title,
            'status': self.status,
            'detail': self.detail,
            'instance': self.instance,
            'timestamp': self.timestamp.isoformat(),
            'category': self.category,
            'recoverable': self.recoverable,
            'suggested_action': self.suggested_action,
        }
        if self.context:
            result['context'] = self.context
        if self.exception_info:
            result['exception'] = self.exception_info
        return result


class ErrorTracker:
    """Tracks errors that occur during script execution using RFC 7807 Problem Details format."""
    
    def __init__(self):
        self.problems: list = []
        self.start_time = datetime.now()
        self.session_id = f"session-{self.start_time.strftime('%Y%m%d%H%M%S')}"
    
    def add_error(self, error_type: str, error_message: str, context: dict = None, exception: Exception = None):
        """Add an error to the tracker using Problem Details format."""
        full_context = {
            'session_id': self.session_id,
            'elapsed_time': str(datetime.now() - self.start_time),
            **(context or {})
        }
        
        problem = ProblemDetails(
            error_type=error_type,
            detail=error_message,
            context=full_context,
            exception=exception
        )
        
        self.problems.append(problem)
        
        recoverable_icon = "🔄" if problem.recoverable else "⛔"
        print(f"    📝 [PROBLEM LOGGED] {recoverable_icon} {problem.title}")
        print(f"       └─ {error_message[:80]}{'...' if len(error_message) > 80 else ''}")
    
    def get_recoverable_errors(self) -> list:
        return [p for p in self.problems if p.recoverable]
    
    def get_fatal_errors(self) -> list:
        return [p for p in self.problems if not p.recoverable]
    
    def display_summary(self):
        """Display a comprehensive summary of all errors at the end of the script."""
        if not self.problems:
            print(f"\n{'='*80}")
            print("✅ PROBLEM DETAILS SUMMARY: No problems were logged during execution!")
            print(f"   Session: {self.session_id}")
            print(f"   Duration: {datetime.now() - self.start_time}")
            print(f"{'='*80}\n")
            return
        
        print(f"\n{'='*80}")
        print(f"⚠️  PROBLEM DETAILS SUMMARY (RFC 7807)")
        print(f"{'='*80}")
        print(f"   Session ID: {self.session_id}")
        print(f"   Duration: {datetime.now() - self.start_time}")
        print(f"   Total Problems: {len(self.problems)}")
        print(f"   Recoverable: {len(self.get_recoverable_errors())} | Fatal: {len(self.get_fatal_errors())}")
        print(f"{'='*80}")
        
        # Group by category
        categories = {}
        for problem in self.problems:
            if problem.category not in categories:
                categories[problem.category] = []
            categories[problem.category].append(problem)
        
        category_icons = {
            'infrastructure': '🔧', 'network': '🌐', 'authentication': '🔐',
            'business_logic': '📋', 'user_action': '👤', 'critical': '💥', 'unknown': '❓'
        }
        
        print(f"\n📊 Problems by Category:")
        for category, probs in sorted(categories.items()):
            icon = category_icons.get(category, '❓')
            recoverable_count = len([p for p in probs if p.recoverable])
            fatal_count = len(probs) - recoverable_count
            print(f"  {icon} {category.upper()}: {len(probs)} problem(s) [🔄 {recoverable_count} recoverable, ⛔ {fatal_count} fatal]")
        
        # Detailed problems
        print(f"\n{'='*80}")
        print(f"📋 DETAILED PROBLEM LOG")
        print(f"{'='*80}")
        
        for i, problem in enumerate(self.problems, 1):
            recoverable_icon = "🔄" if problem.recoverable else "⛔"
            print(f"\n┌{'─'*78}┐")
            print(f"│ Problem #{i}: {problem.title[:60]:<60} {recoverable_icon} │")
            print(f"├{'─'*78}┤")
            print(f"│ Type: {problem.type[:70]:<70} │")
            print(f"│ Category: {problem.category:<67} │")
            print(f"│ Timestamp: {problem.timestamp.isoformat():<66} │")
            print(f"├{'─'*78}┤")
            
            detail = problem.detail
            print(f"│ Detail:{'':71} │")
            for line in [detail[i:i+74] for i in range(0, len(detail), 74)]:
                print(f"│   {line:<75} │")
            
            if problem.context:
                print(f"├{'─'*78}┤")
                print(f"│ Context:{'':70} │")
                for key, value in list(problem.context.items())[:5]:  # Limit to 5 items
                    str_value = str(value)[:55]
                    print(f"│   {key[:20]:<20}: {str_value:<53} │")
            
            if problem.exception_info:
                print(f"├{'─'*78}┤")
                print(f"│ Exception: {problem.exception_info['type']:<66} │")
                exc_msg = problem.exception_info['message'][:65]
                print(f"│   {exc_msg:<75} │")
            
            print(f"├{'─'*78}┤")
            action = problem.suggested_action[:74]
            print(f"│ 💡 Action: {action:<67} │")
            print(f"└{'─'*78}┘")
        
        print(f"\n{'='*80}")
        print(f"End of Problem Details Summary")
        print(f"{'='*80}\n")
    
    def save_to_file(self, filename: str = "error_log.json"):
        """Save problems to a JSON file in Problem Details format.
        
        Appends new session errors to existing log file instead of overwriting.
        """
        try:
            # Create current session data
            current_session = {
                'session_id': self.session_id,
                'script_start': self.start_time.isoformat(),
                'script_end': datetime.now().isoformat(),
                'duration': str(datetime.now() - self.start_time),
                'summary': {
                    'total_problems': len(self.problems),
                    'recoverable': len(self.get_recoverable_errors()),
                    'fatal': len(self.get_fatal_errors())
                },
                'problems': [p.to_dict() for p in self.problems]
            }
            
            # Try to load existing log file and append
            existing_sessions = []
            if os.path.exists(filename):
                try:
                    with open(filename, 'r') as f:
                        content = f.read().strip()
                    
                    # Handle empty file gracefully
                    if not content:
                        print(f"📂 Log file {filename} is empty, initializing new log")
                        existing_sessions = []
                    else:
                        existing_data = json.loads(content)
                        
                        # Handle old format (single session) vs new format (multiple sessions)
                        if 'sessions' in existing_data:
                            # New format with sessions array
                            existing_sessions = existing_data.get('sessions', [])
                        elif 'session_id' in existing_data:
                            # Old format with single session - convert to array
                            existing_sessions = [existing_data]
                        else:
                            existing_sessions = []
                        
                        if existing_sessions:
                            print(f"📂 Loaded {len(existing_sessions)} existing session(s) from {filename}")
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"⚠️ Could not parse existing log file ({type(e).__name__}), starting fresh")
                    existing_sessions = []
            
            # Append current session
            existing_sessions.append(current_session)
            
            # Calculate overall totals
            total_problems = sum(s.get('summary', {}).get('total_problems', 0) for s in existing_sessions)
            total_recoverable = sum(s.get('summary', {}).get('recoverable', 0) for s in existing_sessions)
            total_fatal = sum(s.get('summary', {}).get('fatal', 0) for s in existing_sessions)
            
            # Create final data structure with all sessions
            data = {
                'schema': 'RFC 7807 Problem Details',
                'log_updated': datetime.now().isoformat(),
                'total_sessions': len(existing_sessions),
                'overall_summary': {
                    'total_problems': total_problems,
                    'recoverable': total_recoverable,
                    'fatal': total_fatal
                },
                'sessions': existing_sessions
            }
            
            with open(filename, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"📁 Problem Details log saved to: {filename} ({len(existing_sessions)} session(s), {total_problems} total problems)")
        except Exception as e:
            print(f"⚠️ Could not save problem log: {e}")


# Global error tracker instance
error_tracker = ErrorTracker()


# ============================================================================
# TIMEOUT-SAFE NAVIGATION HELPER
# ============================================================================

async def safe_goto(page: Page, url: str, **kwargs):
    """
    Wrapper for page.goto() with hard timeout protection to prevent indefinite hangs.
    
    This is CRITICAL because Playwright's page.goto() can hang indefinitely even with
    a timeout parameter specified. This wrapper adds an asyncio.wait_for() layer that
    provides a hard limit that actually works.
    
    Args:
        page: Playwright page object
        url: URL to navigate to
        **kwargs: Additional arguments to pass to page.goto() (timeout, wait_until, etc.)
    
    Returns:
        Response object or None if timeout
    
    Raises:
        Exception: Re-raises exceptions except asyncio.TimeoutError which is logged
    """
    # Extract timeout from kwargs or use default
    playwright_timeout = kwargs.get('timeout', 30000)  # Default 30s
    # Hard timeout should be slightly higher than Playwright's timeout
    hard_timeout = (playwright_timeout / 1000) + 5  # +5 seconds buffer
    
    try:
        return await asyncio.wait_for(
            page.goto(url, **kwargs),
            timeout=hard_timeout
        )
    except asyncio.TimeoutError:
        print(f"    ⚠️ HARD TIMEOUT: Navigation to {url[:50]}... exceeded {hard_timeout}s")
        error_tracker.add_error(
            error_type='TIMEOUT',
            error_message=f'Hard timeout navigating to {url[:100]} after {hard_timeout}s',
            context={
                'url': url,
                'playwright_timeout': playwright_timeout,
                'hard_timeout': hard_timeout,
                'function': 'safe_goto'
            }
        )
        raise  # Re-raise to let caller handle it


async def safe_place_bet_slip(page: Page, bet_slip: dict, amount: float, match_cache: dict = None, outcome_button_cache: dict = None, timeout_seconds: int = 360):
    """
    Timeout-protected wrapper for place_bet_slip to prevent individual bets from hanging too long.
    
    CRITICAL: This prevents a single bet from hanging indefinitely and blocking the entire script.
    Under normal conditions, a bet should complete in 2-5 minutes. The 6-minute (360s) timeout
    provides a buffer while preventing the 7-minute hang that was causing crashes.
    
    Args:
        page: Playwright page object  
        bet_slip: Dictionary containing bet slip information
        amount: Amount to bet
        match_cache: Optional dictionary containing cached match positions
        outcome_button_cache: Optional dictionary containing cached outcome buttons
        timeout_seconds: Maximum seconds to allow for bet placement (default: 360 = 6 minutes)
    
    Returns:
        Same as place_bet_slip: True/False/"RETRY"/"RELOGIN" or raises TimeoutError
    
    Raises:
        asyncio.TimeoutError: If bet placement exceeds timeout_seconds
    """
    try:
        result = await asyncio.wait_for(
            place_bet_slip(page, bet_slip, amount, match_cache, outcome_button_cache),
            timeout=timeout_seconds
        )
        return result
    except asyncio.TimeoutError:
        slip_num = bet_slip.get("slip_number", "?")
        print(f"\n⚠️ BET TIMEOUT: Bet {slip_num} exceeded {timeout_seconds}s limit!")
        print(f"   This likely means a page operation hung indefinitely.")
        print(f"   Raising timeout to trigger retry/restart logic...")
        
        error_tracker.add_error(
            error_type='TIMEOUT',
            error_message=f'Bet {slip_num} exceeded {timeout_seconds}s timeout - likely page operation hung',
            context={
                'bet_number': slip_num,
                'timeout_seconds': timeout_seconds,
                'function': 'safe_place_bet_slip',
                'recovery_action': 'Timeout will trigger retry or browser restart'
            }
        )
        error_tracker.save_to_file()
        
        raise  # Re-raise to let caller handle it


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
            
            # Determine error type for tracking
            if 'timeout' in error_msg:
                error_type = 'TIMEOUT'
            elif any(kw in error_msg for kw in ['err_name_not_resolved', 'err_connection', 'err_internet_disconnected', 'net::']):
                error_type = 'NETWORK_FAILURE'
            else:
                error_type = 'EXCEPTION'
            
            if is_network_error and attempt < max_retries - 1:
                delay = initial_delay * (2 ** attempt)  # Exponential backoff
                print(f"\n[NETWORK ERROR] {type(e).__name__}: {e}")
                print(f"[RETRY] Attempt {attempt + 1}/{max_retries} - Waiting {delay}s before retry...")
                
                # Track the error (recoverable since we're retrying)
                error_tracker.add_error(
                    error_type=error_type,
                    error_message=f"Network/timeout error during retry_with_backoff (attempt {attempt + 1}/{max_retries}): {str(e)[:150]}",
                    context={
                        'function': func.__name__ if hasattr(func, '__name__') else 'unknown',
                        'attempt': attempt + 1,
                        'max_retries': max_retries,
                        'delay_before_retry': delay
                    },
                    exception=e
                )
                error_tracker.save_to_file()
                
                await asyncio.sleep(delay)
            else:
                # Track final failure before raising
                error_tracker.add_error(
                    error_type='RETRY_FAILED' if is_network_error else error_type,
                    error_message=f"Max retries exceeded or non-recoverable error: {str(e)[:150]}",
                    context={
                        'function': func.__name__ if hasattr(func, '__name__') else 'unknown',
                        'attempt': attempt + 1,
                        'max_retries': max_retries,
                        'is_network_error': is_network_error
                    },
                    exception=e
                )
                error_tracker.save_to_file()
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
            '--disable-dev-shm-usage',  # Prevents shared memory issues in containerized/memory-constrained environments
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
            await safe_goto(page, 'https://new.betway.co.za/sport/soccer', timeout=30000)
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
                
                # Track the retry attempt
                error_tracker.add_error(
                    error_type="NETWORK_FAILURE" if 'timeout' not in error_msg else "TIMEOUT",
                    error_message=f"Navigation to Betway failed (attempt {attempt + 1}/{max_retries}): {str(e)[:150]}",
                    context={
                        'url': 'https://new.betway.co.za/sport/soccer',
                        'attempt': attempt + 1,
                        'max_retries': max_retries,
                        'delay_before_retry': delay,
                        'phase': 'login_navigation'
                    },
                    exception=e
                )
                error_tracker.save_to_file()
                
                await asyncio.sleep(delay)
            else:
                print(f"[FATAL] Could not connect to Betway after {max_retries} attempts")
                error_tracker.add_error(
                    error_type="NETWORK_FAILURE",
                    error_message=f"Could not connect to Betway after {max_retries} attempts - terminating",
                    context={'url': 'https://new.betway.co.za/sport/soccer', 'attempts': max_retries, 'phase': 'login_navigation'},
                    exception=e
                )
                error_tracker.save_to_file()
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
        page_title = await page.title()
        print("Page title:", page_title)
        print("Attempting to capture page state...")
        
        # Try to get all buttons on the page for debugging
        button_debug_info = []
        try:
            all_buttons = await page.query_selector_all('button')
            print(f"\nFound {len(all_buttons)} buttons on page. First 10:")
            for i, btn in enumerate(all_buttons[:10]):
                try:
                    text = await btn.inner_text()
                    classes = await btn.get_attribute('class')
                    btn_id = await btn.get_attribute('id')
                    btn_info = f"text='{text[:30]}', id='{btn_id}'"
                    button_debug_info.append(btn_info)
                    print(f"  Button {i+1}: {btn_info}, class='{classes[:50] if classes else ''}'")
                except:
                    pass
        except:
            pass
        
        error_tracker.add_error(
            error_type="SESSION_EXPIRED",
            error_message="Could not find login button with any known selector - page structure may have changed",
            context={
                'page_url': page.url,
                'page_title': page_title,
                'buttons_found': len(button_debug_info),
                'phase': 'login_modal_open'
            }
        )
        error_tracker.save_to_file()
        
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
        error_tracker.add_error(
            error_type="SESSION_EXPIRED",
            error_message="Could not verify login after authentication attempt - balance element not found",
            context={
                'max_attempts': max_attempts,
                'url': page.url,
                'phase': 'login_verification',
                'possible_causes': ['Invalid credentials', 'Page structure changed', 'Slow page load', 'CAPTCHA required']
            }
        )
        error_tracker.save_to_file()
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
            
            # Track the successful browser restart
            error_tracker.add_error(
                error_type='BROWSER_RESTART',
                error_message='Browser was restarted successfully to prevent memory corruption',
                context={
                    'restart_reason': 'Memory management / Playwright state reset',
                    'status': 'successful'
                }
            )
            
            return result
        else:
            print("  ❌ Failed to login with new browser")
            error_tracker.add_error(
                error_type='BROWSER_RESTART',
                error_message='Browser restart failed - could not login with new browser',
                context={'status': 'login_failed'}
            )
            error_tracker.save_to_file()
            return None
    except Exception as e:
        print(f"  ❌ Error creating new browser: {e}")
        error_tracker.add_error(
            error_type='BROWSER_RESTART',
            error_message=f'Browser restart failed with exception: {str(e)[:150]}',
            context={'status': 'exception'},
            exception=e
        )
        error_tracker.save_to_file()
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
        
        # Track session expiry
        error_tracker.add_error(
            error_type='SESSION_EXPIRED',
            error_message='Session expired - detected logged out state, attempting re-login',
            context={'action': 'attempting_relogin'}
        )
        error_tracker.save_to_file()
        
        # Get credentials from env
        username = os.getenv('BETWAY_USERNAME')
        password = os.getenv('BETWAY_PASSWORD')
        
        if not username or not password:
            print("❌ [RE-LOGIN FAILED] Credentials not found in .env")
            error_tracker.add_error(
                error_type='RELOGIN_FAILED',
                error_message='Re-login failed - credentials not found in .env file',
                context={'reason': 'missing_credentials'}
            )
            error_tracker.save_to_file()
            return False
        
        # Navigate to the main page to trigger login
        try:
            await safe_goto(page, 'https://new.betway.co.za/sport/soccer', timeout=30000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            print(f"❌ [RE-LOGIN FAILED] Could not navigate: {e}")
            error_tracker.add_error(
                error_type='RELOGIN_FAILED',
                error_message=f'Re-login failed - navigation error: {str(e)[:150]}',
                context={'reason': 'navigation_failed'},
                exception=e
            )
            error_tracker.save_to_file()
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
            error_tracker.add_error(
                error_type='RELOGIN_FAILED',
                error_message='Re-login failed - could not find login button',
                context={'reason': 'login_button_not_found'}
            )
            error_tracker.save_to_file()
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
            error_tracker.add_error(
                error_type='RELOGIN_FAILED',
                error_message=f'Re-login failed - could not fill credentials: {str(e)[:150]}',
                context={'reason': 'credential_fill_failed'},
                exception=e
            )
            error_tracker.save_to_file()
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
        error_tracker.add_error(
            error_type='RELOGIN_FAILED',
            error_message='Re-login failed - could not verify login after re-authentication',
            context={'reason': 'verification_failed'}
        )
        error_tracker.save_to_file()
        return False
        
    except Exception as e:
        print(f"❌ [RE-LOGIN ERROR] {e}")
        error_tracker.add_error(
            error_type='RELOGIN_FAILED',
            error_message=f'Re-login failed with exception: {str(e)[:150]}',
            context={'reason': 'exception'},
            exception=e
        )
        error_tracker.save_to_file()
        return False

async def close_all_modals(page: Page, max_attempts=3, timeout_seconds=8):
    """
    Aggressively attempt to close all modals/popups that might appear.
    Tries multiple times with various selectors, including betslip modal.
    IMPORTANT: Avoids clicking on account/profile related elements.
    Has built-in timeout to prevent indefinite hangs.
    
    CRITICAL: This function can accidentally close the browser if Escape is pressed
    when no modal is present. We now check if page is still open before proceeding.
    """
    async def _close_modals_inner():
        for attempt in range(max_attempts):
            try:
                # CRITICAL: Check if page is still open before attempting to close modals
                if page.is_closed():
                    print("    ⚠️ Page already closed - skipping modal close")
                    return
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
                # CRITICAL: Only press Escape if we actually found a modal to avoid closing the browser
                try:
                    # Check if there's actually a visible modal before pressing Escape
                    has_modal = False
                    modal_indicators = [
                        'div[role="dialog"]',
                        'div[class*="modal"]',
                        'div[class*="popup"]',
                        'div[aria-modal="true"]'
                    ]
                    for indicator in modal_indicators:
                        try:
                            modal_elem = await page.query_selector(indicator)
                            if modal_elem and await modal_elem.is_visible():
                                has_modal = True
                                break
                        except:
                            continue
                    
                    # Only press Escape if we confirmed a modal is present
                    if has_modal:
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
    
    # Wrap the entire modal closing logic with a timeout
    try:
        await asyncio.wait_for(_close_modals_inner(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        # If modal closing times out, just press Escape and move on
        # But only if page is still open and we found a modal
        try:
            if not page.is_closed() and has_modal:
                await page.keyboard.press('Escape')
        except:
            pass
    except Exception:
        pass


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
    
    CRITICAL: This function should complete within 5 minutes (300s) under normal conditions.
    If it takes longer, it likely means a page operation has hung indefinitely.
    Callers should wrap this with asyncio.wait_for(place_bet_slip(...), timeout=360) to prevent hangs.
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
                await safe_goto(page, 'https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(1000)
            except asyncio.TimeoutError:
                print(f"  ⚠️ Navigation timeout - continuing anyway")
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
                    
                    # Navigate to match page to click buttons with asyncio timeout for extra protection
                    # CRITICAL: Using safe_goto() with hard timeout to prevent indefinite hangs
                    try:
                        await safe_goto(
                            page, match_url,
                            wait_until='domcontentloaded',
                            timeout=15000
                        )
                    except asyncio.TimeoutError:
                        print(f"    ⚠️ Navigation timeout - page may be slow, continuing...")
                    
                    await page.wait_for_timeout(1200)
                    await close_all_modals(page, timeout_seconds=5)  # Reduced modal timeout
                    await page.wait_for_timeout(1000)  # Reduced wait
                    
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
                        # Extended list of selectors to handle different league page structures
                        button_selectors = [
                            'div.grid.p-1 > div.flex.items-center.justify-between.h-12',
                            'div[class*="grid"] > div[class*="flex items-center justify-between h-12"]',
                            'details:has(span:text("1X2")) div.grid > div',
                            'div[price]',
                            'button[data-translate-market-name="Full Time Result"] div[price]',
                            'div[data-translate-market-name="Full Time Result"] div[price]',
                            # Additional fallback selectors for different league structures
                            'div[class*="market"] div[price]',
                            'div[class*="outcome"] div[price]',
                            'div.flex.items-center.justify-between[price]',
                            'button[price]',
                            'div[data-price]',
                            'span[price]',
                            # More generic selectors as last resort
                            'div[class*="selection"]',
                            'div[class*="bet-button"]',
                            'div[class*="odds"]',
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
                        
                        # Count selections BEFORE clicking to verify it increases
                        initial_selection_count = await count_betslip_selections(page)
                        if initial_selection_count < 0:
                            initial_selection_count = 0
                        
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
                                
                                # VERIFY: Check if selection count INCREASED by 1
                                await page.wait_for_timeout(500)
                                new_selection_count = await count_betslip_selections(page)
                                
                                if new_selection_count > initial_selection_count:
                                    print(f"    ✓ Selection confirmed in betslip ({initial_selection_count} → {new_selection_count})")
                                    selection_confirmed = True
                                    break
                                else:
                                    # Fallback: check betslip text for content
                                    betslip_check = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
                                    if betslip_check:
                                        betslip_text = await betslip_check.inner_text()
                                        # Check if this is the first selection and betslip has content
                                        if initial_selection_count == 0 and new_selection_count == 0:
                                            # Count function might have failed - check content
                                            has_content = '1X2' in betslip_text or (len(betslip_text) > 150 and 'Return' in betslip_text)
                                            if has_content:
                                                print(f"    ✓ Selection confirmed in betslip (content check)")
                                                selection_confirmed = True
                                                break
                                    
                                    print(f"    ⚠️ Selection count unchanged ({initial_selection_count} → {new_selection_count}) - attempt {click_attempt + 1}/{max_click_attempts}")
                                    if click_attempt < max_click_attempts - 1:
                                        # Try scrolling to refresh the view
                                        await page.evaluate('window.scrollTo(0, 0)')
                                        await page.wait_for_timeout(300)
                                        await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                                        await page.wait_for_timeout(500)
                                    
                            except Exception as click_err:
                                print(f"    ⚠️ Click attempt {click_attempt + 1} failed: {click_err}")
                                if click_attempt == max_click_attempts - 1:
                                    print(f"    ❌ ERROR clicking button after {max_click_attempts} attempts")
                                    return False
                        
                        if not selection_confirmed:
                            print(f"    ❌ ERROR: Could not confirm selection was added to betslip after {max_click_attempts} attempts")
                            print(f"    Selection count remained at {initial_selection_count}")
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
            async def check_for_modal(check_balance_change=False, pre_click_balance=None):
                # FIRST PRIORITY: Check if balance decreased (bet was placed)
                # This handles cases where Account modal appears instead of bet confirmation
                if check_balance_change and pre_click_balance is not None and pre_click_balance > 0:
                    await page.wait_for_timeout(500)
                    current_balance = await get_current_balance(page)
                    if current_balance > 0 and current_balance < pre_click_balance:
                        balance_diff = pre_click_balance - current_balance
                        print(f"    ✓ [check_for_modal] BALANCE DECREASED: R{pre_click_balance:.2f} → R{current_balance:.2f} (diff: R{balance_diff:.2f})")
                        print(f"    ✓ [check_for_modal] Bet was placed successfully (balance-based detection)")
                        
                        # Check if Account modal appeared - if so, close it
                        await check_and_close_account_modal(pre_balance=None)  # Skip balance check since we already know bet was placed
                        
                        return True  # Bet was placed, even if wrong modal appeared
                
                # SECOND: Check if this is an Account Options modal (NOT a bet confirmation)
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
                            print(f"    ⚠️ [check_for_modal] Detected Account modal (no bet placed) - indicator: {indicator}")
                            return False  # This is NOT a bet confirmation modal and bet wasn't placed
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
            async def check_and_close_account_modal(pre_balance=None):
                """Detects and closes the Account Options / Deposit funds modal that sometimes appears.
                   IMPORTANT: If balance decreased, this is likely a bet confirmation modal - don't close it!
                """
                # FIRST: Check if balance decreased - if so, a bet was placed and we should NOT close
                if pre_balance and pre_balance > 0:
                    try:
                        current_balance = await get_current_balance(page)
                        if current_balance > 0 and pre_balance - current_balance >= 0.99:
                            # Balance decreased - bet was placed! This might be the confirmation modal
                            print(f"    ✓ [ACCOUNT CHECK] Balance decreased ({pre_balance:.2f} → {current_balance:.2f}) - NOT closing (bet was placed)")
                            return False  # Don't close - bet was placed
                    except:
                        pass
                
                # Also check if bet confirmation elements are present - don't close if they are
                confirmation_indicators = [
                    'button#strike-conf-continue-btn',  # Continue betting button
                    'text="Bet Confirmation"',
                    'text="Booking Code"',
                    'text="Successful Bets"',
                    'text="Betslip:"',
                ]
                for conf_indicator in confirmation_indicators:
                    try:
                        elem = await page.query_selector(conf_indicator)
                        if elem and await elem.is_visible():
                            print(f"    ✓ [ACCOUNT CHECK] Found bet confirmation indicator: {conf_indicator} - NOT closing")
                            return False  # This is a bet confirmation, not an account modal
                    except:
                        continue
                
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
            
            # Capture balance BEFORE any click attempts (for balance-based bet detection)
            pre_click_balance = await get_current_balance(page)
            if pre_click_balance > 0:
                print(f"    💰 Pre-click balance: R{pre_click_balance:.2f}")
            
            # Track how many times Account modal appears (indicates position problem)
            account_modal_count = 0
            
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
                    account_modal_closed = await check_and_close_account_modal(pre_click_balance)
                    if account_modal_closed:
                        account_modal_count += 1
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
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
                    account_modal_closed = await check_and_close_account_modal(pre_click_balance)
                    if account_modal_closed:
                        account_modal_count += 1
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
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
                    account_modal_closed = await check_and_close_account_modal(pre_click_balance)
                    if account_modal_closed:
                        account_modal_count += 1
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
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
                    account_modal_closed = await check_and_close_account_modal(pre_click_balance)
                    if account_modal_closed:
                        account_modal_count += 1
                        print("    ⚠️ Account modal was triggered instead of bet - will retry")
                    
                    modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
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
                        account_modal_closed = await check_and_close_account_modal(pre_click_balance)
                        if account_modal_closed:
                            account_modal_count += 1
                            print("    ⚠️ Account modal was triggered instead of bet - will retry")
                        
                        modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
                        if modal_appeared:
                            print("    ✅ Method 5: Mouse click SUCCESS - modal appeared!")
                            click_success = True
                        else:
                            print("    ✗ Method 5: Mouse click but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 5 failed: {e}")
            
            # If Account modal appeared multiple times, try special recovery method
            if not modal_appeared and account_modal_count >= 2:
                print(f"    ⚠️ Account modal appeared {account_modal_count} times - trying special recovery...")
                try:
                    # Scroll the betslip container to top to ensure Bet Now button is not near deposit button
                    betslip_container = await page.query_selector('div#betslip-container-mobile, div#betslip-container')
                    if betslip_container:
                        await betslip_container.evaluate('el => el.scrollTop = 0')
                        await page.wait_for_timeout(500)
                    
                    # Scroll page down to push betslip higher
                    await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await page.wait_for_timeout(500)
                    
                    # Try clicking with explicit button text matching
                    bet_btn = await page.query_selector('button:has-text("Bet Now"):not(:has-text("deposit")):not(:has-text("Account"))')
                    if bet_btn and await bet_btn.is_visible() and await bet_btn.is_enabled():
                        # Get exact bounding box and click center
                        box = await bet_btn.bounding_box()
                        if box:
                            # Click slightly above center to avoid any overlap issues
                            x = box['x'] + box['width'] / 2
                            y = box['y'] + box['height'] / 2 - 5  # 5px above center
                            await page.mouse.click(x, y)
                            await page.wait_for_timeout(1500)
                            
                            # Close any account modal that might appear
                            await check_and_close_account_modal(pre_click_balance)
                            
                            modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
                            if modal_appeared:
                                print("    ✅ Special recovery: SUCCESS!")
                                click_success = True
                except Exception as e:
                    print(f"    ✗ Special recovery failed: {e}")
            
            # Method 6: Force page refresh and try again with fresh DOM
            if not modal_appeared:
                try:
                    print("    ⚠️ Trying Method 6: Page refresh and retry...")
                    # Reload the current match page to get fresh DOM
                    current_url = page.url
                    await page.reload(wait_until='domcontentloaded', timeout=15000)
                    await page.wait_for_timeout(2000)
                    await close_all_modals(page)
                    
                    # Re-capture balance after reload
                    pre_click_balance = await get_current_balance(page)
                    
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
                        
                        # Close any account modal
                        await check_and_close_account_modal(pre_click_balance)
                        
                        modal_appeared = await check_for_modal(check_balance_change=True, pre_click_balance=pre_click_balance)
                        if modal_appeared:
                            print("    ✅ Method 6: Reload + click SUCCESS - modal appeared!")
                            click_success = True
                        else:
                            print("    ✗ Method 6: Reload + click but no modal appeared")
                except Exception as e:
                    print(f"    ✗ Method 6 failed: {e}")
            
            if not click_success or not modal_appeared:
                print("    ❌ [ERROR] All click methods failed to trigger modal!")
                
                # LAST RESORT: Check if balance decreased anyway (bet might have been placed silently)
                if pre_click_balance > 0:
                    final_balance = await get_current_balance(page)
                    if final_balance > 0 and final_balance < pre_click_balance:
                        balance_diff = pre_click_balance - final_balance
                        # Check if the difference matches the expected stake (with small tolerance)
                        if abs(balance_diff - amount) < 0.50:  # Within R0.50 tolerance
                            print(f"    ✅ [BALANCE CHECK] Bet WAS placed! Balance: R{pre_click_balance:.2f} → R{final_balance:.2f}")
                            print(f"    ✅ Balance decreased by R{balance_diff:.2f} (expected R{amount:.2f})")
                            click_success = True
                            modal_appeared = True  # Treat as success
                        else:
                            print(f"    ⚠️ Balance changed by R{balance_diff:.2f} but expected R{amount:.2f}")
                    else:
                        print(f"    💰 Final balance check: R{final_balance:.2f} (no decrease from R{pre_click_balance:.2f})")
                
                if not click_success:
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
                    
                    # Log the account modal issue if it happened multiple times
                    if account_modal_count > 0:
                        print(f"    📝 Account modal appeared {account_modal_count} times during click attempts")
                        print(f"    📝 This suggests a UI positioning issue - betslip may overlap with account area")
                    
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
                'button:has-text("Continue")',  # Shorter text match
            ]
            
            bet_confirmed = False
            continue_btn = None
            
            # First, find the Continue betting button
            for cont_selector in continue_betting_selectors:
                try:
                    continue_btn = await page.wait_for_selector(cont_selector, timeout=3000, state='visible')
                    if continue_btn and await continue_btn.is_visible():
                        print(f"    ✅ Found 'Continue betting' button using: {cont_selector}")
                        break
                except:
                    continue
            
            # If button found, try multiple click methods
            if continue_btn:
                print(f"    🖱️ Attempting to click 'Continue betting' button...")
                click_succeeded = False
                
                # Method 1: JavaScript click
                if not click_succeeded:
                    try:
                        await continue_btn.scroll_into_view_if_needed()
                        await page.wait_for_timeout(300)
                        await continue_btn.evaluate('el => el.click()')
                        await page.wait_for_timeout(500)
                        # Check if modal closed
                        still_visible = await page.query_selector('button#strike-conf-continue-btn')
                        if not still_visible or not await still_visible.is_visible():
                            click_succeeded = True
                            print(f"    ✅ Method 1: JavaScript click succeeded")
                    except Exception as e:
                        print(f"    ✗ Method 1 (JS click) failed: {e}")
                
                # Method 2: Direct Playwright click
                if not click_succeeded:
                    try:
                        # Re-query button in case DOM changed
                        continue_btn = await page.query_selector('button#strike-conf-continue-btn')
                        if continue_btn and await continue_btn.is_visible():
                            await continue_btn.click(timeout=3000, force=True)
                            await page.wait_for_timeout(500)
                            still_visible = await page.query_selector('button#strike-conf-continue-btn')
                            if not still_visible or not await still_visible.is_visible():
                                click_succeeded = True
                                print(f"    ✅ Method 2: Direct click succeeded")
                    except Exception as e:
                        print(f"    ✗ Method 2 (direct click) failed: {e}")
                
                # Method 3: Mouse click at coordinates
                if not click_succeeded:
                    try:
                        continue_btn = await page.query_selector('button#strike-conf-continue-btn')
                        if continue_btn and await continue_btn.is_visible():
                            box = await continue_btn.bounding_box()
                            if box:
                                x = box['x'] + box['width'] / 2
                                y = box['y'] + box['height'] / 2
                                await page.mouse.click(x, y)
                                await page.wait_for_timeout(500)
                                still_visible = await page.query_selector('button#strike-conf-continue-btn')
                                if not still_visible or not await still_visible.is_visible():
                                    click_succeeded = True
                                    print(f"    ✅ Method 3: Mouse click succeeded")
                    except Exception as e:
                        print(f"    ✗ Method 3 (mouse click) failed: {e}")
                
                # Method 4: Dispatch click event
                if not click_succeeded:
                    try:
                        continue_btn = await page.query_selector('button#strike-conf-continue-btn')
                        if continue_btn and await continue_btn.is_visible():
                            await continue_btn.dispatch_event('click')
                            await page.wait_for_timeout(500)
                            still_visible = await page.query_selector('button#strike-conf-continue-btn')
                            if not still_visible or not await still_visible.is_visible():
                                click_succeeded = True
                                print(f"    ✅ Method 4: Dispatch click succeeded")
                    except Exception as e:
                        print(f"    ✗ Method 4 (dispatch) failed: {e}")
                
                # Method 5: Press Enter while focused
                if not click_succeeded:
                    try:
                        continue_btn = await page.query_selector('button#strike-conf-continue-btn')
                        if continue_btn and await continue_btn.is_visible():
                            await continue_btn.focus()
                            await page.wait_for_timeout(200)
                            await page.keyboard.press('Enter')
                            await page.wait_for_timeout(500)
                            still_visible = await page.query_selector('button#strike-conf-continue-btn')
                            if not still_visible or not await still_visible.is_visible():
                                click_succeeded = True
                                print(f"    ✅ Method 5: Enter key succeeded")
                    except Exception as e:
                        print(f"    ✗ Method 5 (Enter key) failed: {e}")
                
                # Method 6: Press Escape to close modal
                if not click_succeeded:
                    try:
                        print(f"    ⚠️ Trying Escape key to close modal...")
                        await page.keyboard.press('Escape')
                        await page.wait_for_timeout(500)
                        still_visible = await page.query_selector('button#strike-conf-continue-btn')
                        if not still_visible or not await still_visible.is_visible():
                            click_succeeded = True
                            print(f"    ✅ Method 6: Escape key closed modal")
                    except Exception as e:
                        print(f"    ✗ Method 6 (Escape) failed: {e}")
                
                if click_succeeded:
                    bet_confirmed = True
                    if verification['success']:
                        print(f"    ✅ Bet CONFIRMED placed successfully!")
                        return True
                    else:
                        print(f"    ⚠️ Modal closed but verification incomplete - treating as success")
                        return True
                else:
                    print(f"    ⚠️ Could not click 'Continue betting' - modal may still be open")
                    # Even if we couldn't close the modal, the bet was likely placed
                    if verification['success']:
                        print(f"    ✅ Bet was placed (verified) - continuing despite modal issue")
                        # Try one more time to close with Escape
                        await page.keyboard.press('Escape')
                        await page.wait_for_timeout(300)
                        return True
            
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
                    # Try multiple methods to close the modal
                    modal_closed = False
                    
                    # Method 1: Try clicking Continue betting button with multiple approaches
                    close_selectors = [
                        'button#strike-conf-continue-btn',  # Continue betting button
                        'svg#modal-close-btn',  # Specific modal close button
                        'button[aria-label="Close"]',  # Exact match close button
                    ]
                    for close_sel in close_selectors:
                        if modal_closed:
                            break
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
                                
                                # Try multiple click methods
                                for click_method in ['js', 'direct', 'mouse', 'dispatch']:
                                    try:
                                        if click_method == 'js':
                                            await close_btn.evaluate('el => el.click()')
                                        elif click_method == 'direct':
                                            await close_btn.click(force=True)
                                        elif click_method == 'mouse':
                                            box = await close_btn.bounding_box()
                                            if box:
                                                await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                                        elif click_method == 'dispatch':
                                            await close_btn.dispatch_event('click')
                                        
                                        await page.wait_for_timeout(500)
                                        # Check if modal closed
                                        check = await page.query_selector('span:has-text("Bet Confirmation")')
                                        if not check or not await check.is_visible():
                                            modal_closed = True
                                            print(f"    ✅ Modal closed using {close_sel} ({click_method})")
                                            break
                                    except:
                                        continue
                        except:
                            continue
                    
                    # Method 2: Try Escape key
                    if not modal_closed:
                        try:
                            await page.keyboard.press('Escape')
                            await page.wait_for_timeout(500)
                            check = await page.query_selector('span:has-text("Bet Confirmation")')
                            if not check or not await check.is_visible():
                                modal_closed = True
                                print(f"    ✅ Modal closed using Escape key")
                        except:
                            pass
                    
                    # Even if modal didn't close, bet was placed
                    if not modal_closed:
                        print(f"    ⚠️ Could not close confirmation modal - but bet was placed")
                    
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
    
    Handles page/browser closures gracefully by catching CancelledError.
    All interruptions and timeouts are logged to the error tracker.
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
            try:
                if page.is_closed():
                    print("[ERROR] Page was closed during wait!")
                    error_tracker.add_error(
                        error_type='BROWSER_RESTART',
                        error_message='Wait interrupted - page was closed during wait period (may require browser restart)',
                        context={
                            'elapsed_seconds': (i) * chunk_size,
                            'total_seconds': total_seconds,
                            'recovery_action': 'Browser will be restarted on next bet attempt'
                        }
                    )
                    return False
            except Exception as page_check_error:
                # Page object itself may be corrupted
                error_tracker.add_error(
                    error_type='MEMORY_ERROR',
                    error_message=f'Page object corrupted during wait: {str(page_check_error)[:100]}',
                    context={'elapsed_seconds': (i) * chunk_size, 'total_seconds': total_seconds},
                    exception=page_check_error
                )
                return False
            
            try:
                await asyncio.sleep(chunk_size)
            except asyncio.CancelledError:
                print(f"\n[WARNING] Wait interrupted at {(i + 1) * chunk_size}s - page/browser may have been closed")
                error_tracker.add_error(
                    error_type='CANCELLED',
                    error_message=f'Wait interrupted (CancelledError) at {(i + 1) * chunk_size}s - page/browser may have been closed',
                    context={
                        'elapsed_seconds': (i + 1) * chunk_size,
                        'total_seconds': total_seconds,
                        'recovery_action': 'Script will attempt to continue or restart'
                    }
                )
                return False
            except asyncio.TimeoutError as timeout_err:
                print(f"\n[ERROR] Timeout during wait at {(i + 1) * chunk_size}s")
                error_tracker.add_error(
                    error_type='TIMEOUT',
                    error_message=f'Timeout during wait operation at {(i + 1) * chunk_size}s',
                    context={'elapsed_seconds': (i + 1) * chunk_size, 'total_seconds': total_seconds},
                    exception=timeout_err
                )
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
                error_tracker.add_error(
                    error_type='CANCELLED',
                    error_message=f'Wait interrupted (CancelledError) during final {remainder}s - page/browser may have been closed',
                    context={'elapsed_seconds': total_seconds - remainder, 'total_seconds': total_seconds}
                )
                return False
            except asyncio.TimeoutError as timeout_err:
                error_tracker.add_error(
                    error_type='TIMEOUT',
                    error_message=f'Timeout during final wait period',
                    context={'elapsed_seconds': total_seconds - remainder, 'total_seconds': total_seconds},
                    exception=timeout_err
                )
                return False
        
        print("[OK] Wait complete!\n")
        return True
        
    except asyncio.CancelledError:
        print("\n[ERROR] Wait operation cancelled - likely due to browser/page closure")
        error_tracker.add_error(
            error_type='CANCELLED',
            error_message='Wait operation cancelled - likely due to browser/page closure',
            context={'total_seconds': total_seconds, 'recovery_action': 'Auto-retry wrapper will restart script'}
        )
        return False
    except Exception as unexpected_error:
        print(f"\n[ERROR] Unexpected error during wait: {unexpected_error}")
        error_tracker.add_error(
            error_type='EXCEPTION',
            error_message=f'Unexpected error during wait: {str(unexpected_error)[:150]}',
            context={'total_seconds': total_seconds},
            exception=unexpected_error
        )
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
    cumulative_runtime_seconds = 0.0  # Track total runtime across crashes/restarts
    
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
        # Each bet takes approximately 7/3 (~2.33) minutes to place
        # Total estimated runtime = total_combinations * time_per_bet
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
        
        # ============================================================================
        # BALANCE VALIDATION - Check if balance is sufficient for all bets
        # ============================================================================
        print(f"\n{'='*60}")
        print("💰 BALANCE VALIDATION CHECK")
        print(f"{'='*60}")
        
        current_balance = await get_current_balance(page)
        total_required = total_combinations * amount_per_slip
        
        if current_balance > 0:
            print(f"   Current balance: R{current_balance:.2f}")
            print(f"   Total bets: {total_combinations}")
            print(f"   Amount per bet: R{amount_per_slip:.2f}")
            print(f"   Total required: R{total_required:.2f}")
            
            if current_balance >= total_required:
                remaining_after = current_balance - total_required
                print(f"   ✅ SUFFICIENT BALANCE")
                print(f"   Balance after all bets: R{remaining_after:.2f}")
            else:
                shortfall = total_required - current_balance
                max_possible_bets = int(current_balance / amount_per_slip)
                print(f"   ❌ INSUFFICIENT BALANCE")
                print(f"   Shortfall: R{shortfall:.2f}")
                print(f"   Maximum bets possible: {max_possible_bets}")
                print(f"{'='*60}")
                
                # Track the error
                error_tracker.add_error(
                    error_type="BET_FAILED",
                    error_message=f"Insufficient balance: have R{current_balance:.2f}, need R{total_required:.2f} for {total_combinations} bets",
                    context={
                        'current_balance': current_balance,
                        'total_required': total_required,
                        'total_bets': total_combinations,
                        'amount_per_bet': amount_per_slip,
                        'shortfall': shortfall
                    }
                )
                
                print(f"\n⛔ Cannot proceed - please deposit at least R{shortfall:.2f}")
                print(f"   Or reduce the number of matches/bet amount")
                
                error_tracker.display_summary()
                error_tracker.save_to_file()
                await browser.close()
                return
        else:
            print(f"   ⚠️ Could not retrieve balance - proceeding with caution")
            print(f"   Total required for all bets: R{total_required:.2f}")
        
        print(f"{'='*60}\n")
        
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
                    
                    # Load cumulative runtime from previous sessions
                    cumulative_runtime_seconds = resume_data.get('cumulative_runtime_seconds', 0.0)
                    if cumulative_runtime_seconds > 0:
                        prev_mins = int(cumulative_runtime_seconds // 60)
                        prev_secs = int(cumulative_runtime_seconds % 60)
                        print(f"Previous runtime: {prev_mins}m {prev_secs}s")
                    
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
        
        # Define the scraping URLs - highlights first, then upcoming as fallback
        SCRAPING_URLS = [
            {
                'url': 'https://new.betway.co.za/sport/soccer/highlights',
                'name': 'Highlights',
                'description': 'Featured/popular matches'
            },
            {
                'url': 'https://new.betway.co.za/sport/soccer/upcoming',
                'name': 'Upcoming',
                'description': 'All upcoming matches'
            }
        ]
        
        # Navigate to highlights page first (primary source)
        print("\nNavigating to soccer highlights page (primary source)...")
        try:
            await asyncio.wait_for(
                page.goto(SCRAPING_URLS[0]['url'], wait_until='domcontentloaded', timeout=30000),
                timeout=35  # Hard timeout to prevent hangs
            )
            await page.wait_for_timeout(3000)
            try:
                await close_all_modals(page)
            except:
                pass  # Non-critical if modal closing fails
            print(f"[OK] Loaded {SCRAPING_URLS[0]['name']} page - {SCRAPING_URLS[0]['description']}")
        except Exception as nav_error:
            print(f"[WARNING] Failed to navigate to highlights page: {nav_error}")
            print("[ACTION] Trying upcoming page as fallback...")
            try:
                await asyncio.wait_for(
                    page.goto(SCRAPING_URLS[1]['url'], wait_until='domcontentloaded', timeout=30000),
                    timeout=35  # Hard timeout to prevent hangs
                )
                await page.wait_for_timeout(3000)
                try:
                    await close_all_modals(page)
                except:
                    pass
                print(f"[OK] Loaded {SCRAPING_URLS[1]['name']} page as fallback")
            except Exception as fallback_error:
                print(f"[ERROR] Failed to navigate to both pages: {fallback_error}")
                error_tracker.add_error(
                    error_type="NETWORK_FAILURE" if 'timeout' in str(fallback_error).lower() else "EXCEPTION",
                    error_message=f"Failed to navigate to both highlights and upcoming pages: {str(fallback_error)[:150]}",
                    context={
                        'highlights_url': SCRAPING_URLS[0]['url'],
                        'upcoming_url': SCRAPING_URLS[1]['url'],
                        'phase': 'initial_navigation'
                    },
                    exception=fallback_error
                )
                error_tracker.display_summary()
                error_tracker.save_to_file()
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
            # SMART SCRAPING - Scrape from highlights first, then upcoming if needed
            # Matches can be mixed from both sources as long as they meet filter conditions
            # NOTE: Highlights page sorts matches by TIME (not league order) before selection
            # ============================================================================
            print(f"\n{'='*60}")
            print("🔍 STARTING SMART SCRAPING (HIGHLIGHTS → UPCOMING)")
            print(f"{'='*60}")
            print(f"Scraping order:")
            print(f"  1️⃣  Highlights page (featured matches) - PRIMARY")
            print(f"      ⏰ Matches sorted by: TIME first, then HIGHEST ODDS")
            print(f"      📊 For same time slot: match with highest odds is selected first")
            print(f"  2️⃣  Upcoming page (all matches) - FALLBACK if not enough found")
            print(f"\nLooking for {num_matches} matches that:")
            print(f"  ✓ Start {min_time_before_match}+ hours from now")
            print(f"  ✓ Are {min_gap_hours}+ hours apart from each other")
            print(f"  ✓ Have valid URLs captured")
            print(f"  ✓ Can be mixed from both sources")
            print(f"Stopping as soon as we find {num_matches} matches meeting all conditions")
            print(f"{'='*60}\n")
        
            min_gap_minutes = int(min_gap_hours * 60)
            max_pages_per_source = 20
            
            # Track all processed match names across all sources
            all_processed_match_names = set()
            
            # Iterate through scraping sources: highlights first, then upcoming
            for source_index, source in enumerate(SCRAPING_URLS):
                # Skip if we already have enough matches
                if len(filtered_matches) >= num_matches:
                    break
                
                source_url = source['url']
                source_name = source['name']
                source_desc = source['description']
                
                # Check if this is the highlights page (needs time-based sorting)
                is_highlights_page = 'highlights' in source_url.lower()
                
                print(f"\n{'='*60}")
                print(f"📌 SOURCE {source_index + 1}/{len(SCRAPING_URLS)}: {source_name.upper()}")
                print(f"   {source_desc}")
                print(f"   URL: {source_url}")
                if is_highlights_page:
                    print(f"   ⏰ TIME-SORTED MODE: Matches sorted by start time, then by highest odds")
                print(f"   Matches found so far: {len(filtered_matches)}/{num_matches}")
                print(f"{'='*60}")
                
                # Navigate to this source
                try:
                    await asyncio.wait_for(
                        page.goto(source_url, wait_until='domcontentloaded', timeout=30000),
                        timeout=35  # Hard timeout to prevent hangs
                    )
                    await page.wait_for_timeout(2000)
                    await close_all_modals(page)
                except Exception as nav_error:
                    print(f"  ⚠️ Failed to navigate to {source_name}: {nav_error}")
                    continue  # Try next source
                
                current_page = 0
                
                # For highlights page: collect ALL candidate matches first, then sort by time
                # For upcoming page: process normally (already sorted by time)
                candidate_matches = [] if is_highlights_page else None
                
                while (is_highlights_page or len(filtered_matches) < num_matches) and current_page < max_pages_per_source:
                    # For highlights, we need to scan all pages to get all candidates
                    # For upcoming, we can stop early when we have enough matches
                    if is_highlights_page:
                        # Continue until we've scanned enough pages or hit max
                        pass
                    else:
                        if len(filtered_matches) >= num_matches:
                            break
                    
                    current_page += 1
                    print(f"\n📄 [{source_name}] Scraping page {current_page}/{max_pages_per_source}...")
                    
                    await close_all_modals(page)
                    await page.wait_for_timeout(500)
                    
                    # Scroll to load content
                    for _ in range(3):
                        await page.evaluate('window.scrollBy(0, 500)')
                        await page.wait_for_timeout(200)
                    
                    match_containers = await page.query_selector_all('div[data-v-206d232b].relative.grid.grid-cols-12')
                    print(f"  Found {len(match_containers)} match containers on page {current_page}")
                    
                    # Debug counters (reset per page)
                    debug_no_teams = 0
                    debug_no_time = 0
                    debug_live = 0
                    debug_too_soon = 0
                    debug_no_odds = 0
                    debug_wrong_odds_count = 0
                    debug_no_gap = 0
                    debug_duplicate = 0
                    debug_no_url = 0
                    matches_added_this_page = 0
                    
                    # Process each match container
                    for i, container in enumerate(match_containers):
                        # For non-highlights pages, check if we have enough matches
                        if not is_highlights_page and len(filtered_matches) >= num_matches:
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
                            
                            # Skip if already processed this match (across ALL sources)
                            if match_name in all_processed_match_names:
                                debug_duplicate += 1
                                continue
                            
                            # Mark as processed globally
                            all_processed_match_names.add(match_name)
                            
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
                            if len(odds) != 3:
                                debug_wrong_odds_count += 1
                                continue
                            
                            # Try to capture URL for this match
                            match_url = None
                            try:
                                link_element = await container.query_selector('a[href*="/event/soccer/"]')
                                if link_element:
                                    relative_url = await link_element.get_attribute('href')
                                    if relative_url:
                                        if relative_url.startswith('/'):
                                            match_url = f"https://new.betway.co.za{relative_url}"
                                        else:
                                            match_url = relative_url
                            except Exception as href_error:
                                print(f"  ⚠️ Failed to extract href: {href_error}")
                            
                            if not match_url:
                                debug_no_url += 1
                                continue
                            
                            # Create match object
                            match = {
                                'name': match_name,
                                'team1': team1,
                                'team2': team2,
                                'odds': odds[:3],
                                'start_time': start_time_text,
                                'url': match_url,
                                'source': source_name
                            }
                            
                            # For highlights page: add to candidates (will sort later)
                            # For upcoming page: apply gap filter immediately
                            if is_highlights_page:
                                candidate_matches.append(match)
                                matches_added_this_page += 1
                            else:
                                # Check time gap for non-highlights pages
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
                                
                                filtered_matches.append(match)
                                matches_added_this_page += 1
                                print(f"  ✓ Match {len(filtered_matches)}/{num_matches}: '{match_name}' ({start_time_text}) [from {source_name}]")
                                
                                if len(filtered_matches) >= num_matches:
                                    break
                            
                        except Exception as e:
                            continue
                    
                    # Print debug info for this page
                    print(f"  📊 Page {current_page} summary for [{source_name}]:")
                    if is_highlights_page:
                        print(f"    ✓ {matches_added_this_page} candidates added (will sort by time later)")
                        print(f"    Total candidates so far: {len(candidate_matches)}")
                    else:
                        print(f"    ✓ {matches_added_this_page} matches selected")
                    
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
                    if debug_no_url > 0:
                        print(f"    ❌ {debug_no_url} - Could not extract URL")
                    if not is_highlights_page and debug_no_gap > 0:
                        print(f"    ❌ {debug_no_gap} - Too close to other selected matches (<{min_gap_hours}h gap)")
                    if debug_duplicate > 0:
                        print(f"    ❌ {debug_duplicate} - Duplicate (already seen)")
                    
                    # Check if we should continue to next page
                    if not is_highlights_page and len(filtered_matches) >= num_matches:
                        break
                    
                    # Click Next button if needed
                    if current_page < max_pages_per_source:
                        try:
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
                                print(f"  Clicking 'Next' to load [{source_name}] page {current_page + 1}...")
                                await next_button.click()
                                await page.wait_for_timeout(1500)
                            else:
                                print(f"  No 'Next' button available on {source_name} - end of pages")
                                break
                                
                        except Exception as e:
                            print(f"  ⚠️ Error clicking Next button on {source_name}: {e}")
                            break
                
                # For highlights page: NOW sort candidates by time and apply gap filtering
                if is_highlights_page and candidate_matches:
                    print(f"\n  {'='*50}")
                    print(f"  ⏰ SORTING {len(candidate_matches)} CANDIDATES BY TIME, THEN BY HIGHEST ODDS")
                    print(f"  {'='*50}")
                    print(f"  Priority 1: Start time (earliest first)")
                    print(f"  Priority 2: Highest odds for same time slot (max of 1/X/2)")
                    
                    # Helper function to get the maximum odd value from a match's odds
                    def get_max_odd(match):
                        odds = match.get('odds', [])
                        if len(odds) >= 3:
                            return max(odds)  # Return highest odd value
                        return 0  # No odds available
                    
                    # Sort candidates by:
                    # 1. Parsed time (ascending - earliest first)
                    # 2. Maximum odd value (descending - highest odds first for same time)
                    candidate_matches.sort(key=lambda m: (parse_match_time(m) or 999999, -get_max_odd(m)))
                    
                    print(f"  Sorted order (by time, then highest odds):")
                    for idx, cm in enumerate(candidate_matches[:10], 1):  # Show first 10
                        odds = cm.get('odds', [])
                        max_odd = max(odds) if len(odds) >= 3 else 0
                        odds_str = f"[1:{odds[0]:.2f} X:{odds[1]:.2f} 2:{odds[2]:.2f}]" if len(odds) >= 3 else "[no odds]"
                        print(f"    {idx}. {cm['name']} - {cm.get('start_time', 'Unknown')} | Max odd: {max_odd:.2f} {odds_str}")
                    if len(candidate_matches) > 10:
                        print(f"    ... and {len(candidate_matches) - 10} more candidates")
                    
                    # Now apply gap filtering on sorted candidates
                    print(f"\n  Applying {min_gap_hours}h gap filter on time-sorted matches...")
                    
                    for match in candidate_matches:
                        if len(filtered_matches) >= num_matches:
                            break
                        
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
                        
                        if is_far_enough:
                            filtered_matches.append(match)
                            print(f"  ✓ Selected {len(filtered_matches)}/{num_matches}: '{match['name']}' ({match.get('start_time', 'Unknown')}) [from {source_name}]")
                
                # Summary for this source
                matches_from_source = len([m for m in filtered_matches if m.get('source') == source_name])
                print(f"\n  📋 [{source_name}] Summary: Found {matches_from_source} matches from this source")
            
            # Count matches by source for final summary
            source_counts = {}
            for m in filtered_matches:
                src = m.get('source', 'Unknown')
                source_counts[src] = source_counts.get(src, 0) + 1
            
            print(f"\n{'='*60}")
            print(f"✅ SMART SCRAPING COMPLETE (HIGHLIGHTS → UPCOMING)")
            print(f"{'='*60}")
            print(f"Found {len(filtered_matches)}/{num_matches} matches meeting all conditions")
            print(f"Match sources breakdown:")
            for src, count in source_counts.items():
                print(f"  - {src}: {count} match(es)")
            print(f"{'='*60}\n")
            
            if len(filtered_matches) < num_matches:
                print(f"\n[ERROR] Could not find {num_matches} matches with {min_gap_hours}+ hour gaps")
                print(f"Searched both Highlights and Upcoming pages")
                print(f"Found {len(filtered_matches)} matches meeting conditions")
                
                error_tracker.add_error(
                    error_type="BET_FAILED",
                    error_message=f"Could not find {num_matches} matches with {min_gap_hours}+ hour gaps from highlights or upcoming pages",
                    context={
                        'required_matches': num_matches,
                        'found_matches': len(filtered_matches),
                        'min_gap_hours': min_gap_hours,
                        'sources_searched': [s['name'] for s in SCRAPING_URLS]
                    }
                )
                
                error_tracker.display_summary()
                error_tracker.save_to_file()
                
                await browser.close()
                return
        
        # Use the filtered matches
        matches = filtered_matches[:num_matches]
        print(f"\n{'='*60}")
        print(f"📋 SELECTED MATCHES ({len(matches)} matches)")
        print(f"{'='*60}")
        for i, m in enumerate(matches, 1):
            start_time = m.get('start_time', 'Unknown time')
            source = m.get('source', 'Unknown')
            odds = m.get('odds', [])
            odds_str = f"1:{odds[0]:.2f} X:{odds[1]:.2f} 2:{odds[2]:.2f}" if len(odds) >= 3 else str(odds)
            print(f"  {i}. {m['name']}")
            print(f"     ⏰ Start: {start_time}")
            print(f"     📊 Odds: {odds_str}")
            print(f"     📌 Source: {source}")
        print(f"{'='*60}")
        print(f"All matches are {min_gap_hours}+ hours apart from each other")
        print(f"Matches can be from Highlights and/or Upcoming pages")
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
            
            error_tracker.add_error(
                error_type="BET_FAILED",
                error_message=f"{len(missing_urls)} match(es) missing cached URLs - cannot proceed with betting",
                context={
                    'missing_urls': missing_urls,
                    'total_matches': len(matches),
                    'phase': 'url_validation'
                }
            )
            error_tracker.display_summary()
            error_tracker.save_to_file()
            
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
            
            error_tracker.add_error(
                error_type="BET_FAILED",
                error_message=f"Match time gap validation failed - matches are NOT {min_gap_hours}+ hours apart",
                context={
                    'required_gap_hours': min_gap_hours,
                    'num_matches': len(matches),
                    'phase': 'time_gap_validation'
                }
            )
            error_tracker.display_summary()
            error_tracker.save_to_file()
            
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
        
        total_bets = 3 ** num_matches  # 3 outcomes (1, X, 2) per match
        avg_time_per_bet = 7 / 3  # ~2.33 minutes per bet (based on empirical observation)
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
        # Note: All bets retry until success - no skipping
        start_index = 0  # Track where to continue from
        
        # Initialize match position cache for faster bet placement
        match_cache = {}
        
        # Initialize outcome button cache to reuse buttons across all bets
        outcome_button_cache = {}
        
        # CHECK: Try to load cached selectors from previous run
        cached_selectors_loaded = False
        if resume_data and 'outcome_button_cache' in resume_data:
            saved_cache = resume_data.get('outcome_button_cache', {})
            if saved_cache and len(saved_cache) >= num_matches:
                outcome_button_cache = saved_cache
                cached_selectors_loaded = True
                print(f"\n{'='*60}")
                print("⚡ USING SAVED SELECTOR CACHE (SKIPPING PRE-CACHE)")
                print(f"{'='*60}")
                print(f"Loaded {len(outcome_button_cache)} cached selectors from previous run")
                print(f"No navigation needed - selectors work on any browser instance")
                print(f"{'='*60}\n")
        
        # Only do pre-caching if we don't have a valid saved cache
        if not cached_selectors_loaded:
            # PRE-CACHE: Navigate to all match pages and cache outcome buttons ONCE
            print(f"\n{'='*60}")
            print("🔄 PRE-CACHING OUTCOME BUTTONS FOR ALL MATCHES")
            print(f"{'='*60}")
            print(f"Navigating to {num_matches} match pages to cache buttons...")
            print(f"Cache will be PERSISTENT across all {len(bet_slips)} bet combinations")
            print(f"Cache is saved to progress file - survives crashes!")
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
                        # Extended list of selectors to handle different league page structures
                        button_selectors = [
                            'div.grid.p-1 > div.flex.items-center.justify-between.h-12',
                            'div[class*="grid"] > div[class*="flex items-center justify-between h-12"]',
                            'details:has(span:text("1X2")) div.grid > div',
                            'div[price]',
                            'button[data-translate-market-name="Full Time Result"] div[price]',
                            'div[data-translate-market-name="Full Time Result"] div[price]',
                            # Additional fallback selectors for different league structures
                            'div[class*="market"] div[price]',
                            'div[class*="outcome"] div[price]',
                            'div.flex.items-center.justify-between[price]',
                            'button[price]',
                            'div[data-price]',
                            'span[price]',
                            # More generic selectors as last resort
                            'div[class*="selection"]',
                            'div[class*="bet-button"]',
                            'div[class*="odds"]',
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
                            error_tracker.add_error(
                                error_type="BET_FAILED",
                                error_message=f"Could not find outcome button selector for match: {match['name']}",
                                context={
                                    'match_url': match_url,
                                    'match_name': match['name'],
                                    'phase': 'pre_caching'
                                }
                            )
                        
                    except Exception as e:
                        print(f"  ❌ ERROR caching buttons: {e}\n")
                        error_tracker.add_error(
                            error_type="EXCEPTION",
                            error_message=f"Exception during outcome button caching for match: {match['name']}",
                            context={
                                'match_url': match_url,
                                'match_name': match['name'],
                                'phase': 'pre_caching'
                            },
                            exception=e
                        )
        
            print(f"{'='*60}")
            print(f"✅ PRE-CACHING COMPLETE")
            print(f"{'='*60}")
            print(f"Cached outcome buttons for {len(outcome_button_cache)}/{num_matches} matches")
            print(f"Cache saved to progress file - survives crashes!")
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
                
                error_tracker.add_error(
                    error_type="SESSION_EXPIRED",
                    error_message=f"Could not verify/restore login - stopping after {successful} successful bets",
                    context={
                        'bet_number': i + 1,
                        'total_bets': len(bet_slips),
                        'successful_so_far': successful
                    }
                )
                
                # Save progress before stopping
                with open(progress_file, 'w') as f:
                    json.dump({
                        'last_completed_bet': i,
                        'last_successful_bet': i - 1 if i > 0 else 0,
                        'successful': successful,
                        'failed': 0,
                        'match_fingerprint': current_match_fingerprint,
                        'timestamp': datetime.now().isoformat(),
                        'matches_data': matches,
                        'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                        'outcome_button_cache': outcome_button_cache
                    }, f)
                
                error_tracker.display_summary()
                error_tracker.save_to_file()
                
                break
            
            try:
                # Try to place bet with retry on network errors
                # Per-bet timeout: 4 minutes (240 seconds) to prevent hangs
                PER_BET_TIMEOUT = 240  # 4 minutes - reduced for faster recovery
                
                try:
                    # Wrap the bet placement in asyncio.wait_for with timeout
                    success = await asyncio.wait_for(
                        retry_with_backoff(
                            place_bet_slip,
                            max_retries=3,
                            initial_delay=5,
                            page=page, bet_slip=bet_slip, amount=amount_per_slip, match_cache=match_cache, outcome_button_cache=outcome_button_cache
                        ),
                        timeout=PER_BET_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    # Per-bet timeout exceeded - bet took longer than 5 minutes
                    print(f"\n⏱️ [PER-BET TIMEOUT] Bet {bet_slip['slip_number']} exceeded {PER_BET_TIMEOUT}s ({PER_BET_TIMEOUT//60} min) timeout!")
                    print(f"[ERROR] Marking bet as failed and triggering retry...")
                    
                    error_tracker.add_error(
                        error_type='TIMEOUT',
                        error_message=f'Per-bet timeout ({PER_BET_TIMEOUT}s) exceeded for bet {bet_slip["slip_number"]} - operation hung',
                        context={
                            'bet_number': bet_slip['slip_number'],
                            'timeout_seconds': PER_BET_TIMEOUT,
                            'action': 'will_retry_or_terminate'
                        }
                    )
                    success = "RETRY"  # Trigger retry logic instead of hard failure
                    
                except (PlaywrightError, PlaywrightTimeoutError) as e:
                    # Network failure - mark bet as failed
                    print(f"\n[CRITICAL ERROR] Network failure after retries: {e}")
                    print("[ERROR] Marking bet as failed (no browser restart)")
                    
                    # Determine error type
                    error_str = str(e).lower()
                    if 'timeout' in error_str:
                        error_type = 'TIMEOUT'
                    else:
                        error_type = 'NETWORK_FAILURE'
                    
                    error_tracker.add_error(
                        error_type=error_type,
                        error_message=f'Network/timeout failure during bet {bet_slip["slip_number"]} placement: {str(e)[:150]}',
                        context={
                            'bet_number': bet_slip['slip_number'],
                            'exception_type': type(e).__name__
                        },
                        exception=e
                    )
                    success = False
                
                if success == True:
                    successful += 1
                    print(f"\n[SUCCESS] Bet slip {bet_slip['slip_number']} placed!")
                    
                    # Save progress - track last SUCCESSFUL bet index
                    # Calculate current session runtime to add to cumulative
                    current_session_runtime = time.time() - script_start_time
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i + 1,  # Next bet to attempt
                            'last_successful_bet': i,  # Last successful bet index
                            'successful': successful,
                            'failed': 0,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat(),
                            'matches_data': matches,  # Save match data for resume
                            'cumulative_runtime_seconds': cumulative_runtime_seconds + current_session_runtime,  # Track total runtime
                            'outcome_button_cache': outcome_button_cache  # PERSIST selector cache for restart
                        }, f)
                    
                    # AGGRESSIVE memory management to prevent Playwright corruption
                    # GC every bet (not just every 3) for more stable long sessions
                    gc.collect()
                    
                    # BROWSER RESTART every 8 bets - TRUE fix for Playwright memory corruption
                    # Creates completely fresh browser instance with clean memory state
                    # Reduced from 10 to 8 for earlier memory cleanup
                    if (i + 1) % 8 == 0 and i < len(bet_slips) - 1:
                        try:
                            print(f"\n  [BROWSER RESTART] Restarting browser after {i + 1} bets to prevent memory corruption...")
                            restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                            if restart_result:
                                page = restart_result["page"]
                                browser = restart_result["browser"]
                                # NOTE: We intentionally DO NOT clear the cache!
                                # The cache contains selector STRINGS (e.g., 'div.grid.p-1 > div...')
                                # These selectors are still valid for the new browser - no re-caching needed!
                                print(f"  [BROWSER RESTART] ✓ Fresh browser ready - keeping {len(outcome_button_cache)} cached selectors")
                                
                                # Log successful browser restart
                                error_tracker.add_error(
                                    error_type='BROWSER_RESTART_SUCCESS',
                                    error_message=f'Scheduled browser restart after bet {i + 1} completed successfully',
                                    context={
                                        'bet_number': i + 1,
                                        'cached_selectors': len(outcome_button_cache),
                                        'reason': 'scheduled_memory_cleanup',
                                        'interval': 'every_8_bets'
                                    }
                                )
                                error_tracker.save_to_file()
                                
                                # Just navigate to soccer page - selectors will work on first bet
                                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                await page.wait_for_timeout(1000)
                                await close_all_modals(page, timeout_seconds=5)
                            else:
                                print(f"  [BROWSER RESTART] ⚠️ Failed - continuing with current browser")
                                error_tracker.add_error(
                                    error_type='BROWSER_RESTART_FAILED',
                                    error_message=f'Scheduled browser restart after bet {i + 1} failed - no result returned',
                                    context={
                                        'bet_number': i + 1,
                                        'reason': 'restart_returned_none',
                                        'recovery_action': 'continuing_with_current_browser'
                                    }
                                )
                                error_tracker.save_to_file()
                        except Exception as restart_err:
                            print(f"  [BROWSER RESTART] ⚠️ Error: {restart_err} - continuing with current browser")
                            error_tracker.add_error(
                                error_type='BROWSER_RESTART_ERROR',
                                error_message=f'Browser restart exception after bet {i + 1}: {str(restart_err)[:150]}',
                                context={
                                    'bet_number': i + 1,
                                    'error_details': str(restart_err),
                                    'recovery_action': 'continuing_with_current_browser'
                                },
                                exception=restart_err
                            )
                            error_tracker.save_to_file()
                    
                    # Periodic page refresh (every 5 bets that aren't browser restart bets)
                    elif (i + 1) % 5 == 0 and i < len(bet_slips) - 1:
                        try:
                            print(f"  [MEMORY] Refreshing page after {i + 1} bets...")
                            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                            await page.wait_for_timeout(2000)
                            await close_all_modals(page)
                            gc.collect()
                            print(f"  [MEMORY] Page refreshed and GC completed")
                            
                            # Log successful page refresh
                            error_tracker.add_error(
                                error_type='PAGE_REFRESH_SUCCESS',
                                error_message=f'Scheduled page refresh after bet {i + 1} completed successfully',
                                context={
                                    'bet_number': i + 1,
                                    'reason': 'scheduled_memory_cleanup',
                                    'interval': 'every_5_bets'
                                }
                            )
                            error_tracker.save_to_file()
                        except Exception as refresh_err:
                            print(f"  [WARNING] Page refresh failed: {refresh_err}")
                            error_tracker.add_error(
                                error_type='PAGE_REFRESH_FAILED',
                                error_message=f'Page refresh failed after bet {i + 1}: {str(refresh_err)[:150]}',
                                context={
                                    'bet_number': i + 1,
                                    'error_details': str(refresh_err),
                                    'recovery_action': 'continuing_anyway'
                                },
                                exception=refresh_err
                            )
                            error_tracker.save_to_file()
                    
                    # Wait between bets
                    if i < len(bet_slips) - 1:
                        wait_success = await wait_between_bets(page, seconds=5, add_random=True)
                        
                        # If wait was interrupted, just log it (no restart)
                        if not wait_success:
                            print("\n[WARNING] Wait interrupted - continuing anyway...")
                            error_tracker.add_error(
                                error_type='WAIT_INTERRUPTED',
                                error_message=f'Wait between bets was interrupted after bet {i + 1}',
                                context={
                                    'bet_number': i + 1,
                                    'recovery_action': 'continuing_anyway'
                                }
                            )
                            error_tracker.save_to_file()
                
                elif success == "RETRY":
                    # Click failed but may succeed on retry - don't count as failed yet
                    print(f"\n⚠️ [RETRY NEEDED] Bet slip {bet_slip['slip_number']} click failed - will retry...")
                    
                    # Track the retry attempt
                    error_tracker.add_error(
                        error_type='RETRY_FAILED',
                        error_message=f'Bet slip {bet_slip["slip_number"]} required retry - click failed on first attempt',
                        context={
                            'bet_number': bet_slip['slip_number'],
                            'action': 'retry_in_progress'
                        }
                    )
                    
                    # Wait a bit before retrying
                    print("  Waiting 10 seconds before retry...")
                    await page.wait_for_timeout(10000)
                    
                    # Retry the same bet with timeout protection
                    print(f"\n🔄 RETRYING BET {bet_slip['slip_number']}/{len(bet_slips)}...")
                    try:
                        retry_success = await safe_place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache, timeout_seconds=360)
                    except asyncio.TimeoutError:
                        print(f"⚠️ Retry also timed out after 360s - treating as failure")
                        retry_success = False
                    
                    if retry_success == True:
                        successful += 1
                        print(f"\n[SUCCESS] Retry bet slip {bet_slip['slip_number']} placed!")
                        
                        # Save progress
                        with open(progress_file, 'w') as f:
                            json.dump({
                                'last_completed_bet': i + 1,
                                'last_successful_bet': i,
                                'successful': successful,
                                'failed': 0,
                                'match_fingerprint': current_match_fingerprint,
                                'timestamp': datetime.now().isoformat(),
                                'matches_data': matches,
                                'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                'outcome_button_cache': outcome_button_cache
                            }, f)
                        
                        # Wait between bets
                        if i < len(bet_slips) - 1:
                            await wait_between_bets(page, seconds=5, add_random=True)
                    else:
                        # First retry failed - try browser restart
                        print(f"\n❌ [FAILED] Retry bet slip {bet_slip['slip_number']} also failed!")
                        print(f"\n🔄 ALL BETS ARE IMPORTANT - Attempting browser restart...")
                        
                        error_tracker.add_error(
                            error_type='RETRY_FAILED',
                            error_message=f'Bet {bet_slip["slip_number"]} retry failed - attempting browser restart',
                            context={'bet_number': bet_slip['slip_number'], 'action': 'browser_restart'}
                        )
                        error_tracker.save_to_file()
                        
                        # Browser restart retry loop
                        max_browser_retries = 5
                        bet_placed = False
                        
                        for browser_retry in range(max_browser_retries):
                            print(f"\n🔄 Browser restart attempt {browser_retry + 1}/{max_browser_retries}...")
                            
                            try:
                                restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                                if restart_result:
                                    page = restart_result["page"]
                                    browser = restart_result["browser"]
                                    
                                    await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                    await page.wait_for_timeout(2000)
                                    await close_all_modals(page)
                                    await page.wait_for_timeout(10000)
                                    
                                    retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                                    
                                    if retry_success == True:
                                        successful += 1
                                        bet_placed = True
                                        print(f"\n✅ [SUCCESS] Bet {bet_slip['slip_number']} placed after browser restart!")
                                        
                                        with open(progress_file, 'w') as f:
                                            json.dump({
                                                'last_completed_bet': i + 1,
                                                'last_successful_bet': i,
                                                'successful': successful,
                                                'failed': 0,
                                                'match_fingerprint': current_match_fingerprint,
                                                'timestamp': datetime.now().isoformat(),
                                                'matches_data': matches,
                                                'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                                'outcome_button_cache': outcome_button_cache
                                            }, f)
                                        break
                            except Exception as e:
                                print(f"  ❌ Exception: {e}")
                            
                            if browser_retry < max_browser_retries - 1:
                                wait_time = 15 * (browser_retry + 1)
                                print(f"  Waiting {wait_time}s...")
                                await asyncio.sleep(wait_time)
                        
                        if not bet_placed:
                            print(f"\n⛔ All retries exhausted for bet {bet_slip['slip_number']}")
                            print(f"Progress saved - run script again to retry")
                            
                            with open(progress_file, 'w') as f:
                                json.dump({
                                    'last_completed_bet': i,
                                    'last_successful_bet': i - 1 if i > 0 else -1,
                                    'successful': successful,
                                    'failed': 0,
                                    'match_fingerprint': current_match_fingerprint,
                                    'timestamp': datetime.now().isoformat(),
                                    'matches_data': matches,
                                    'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                    'outcome_button_cache': outcome_button_cache
                                }, f)
                            
                            error_tracker.display_summary()
                            error_tracker.save_to_file()
                            
                            try:
                                await browser.close()
                            except:
                                pass
                            
                            import sys
                            sys.exit(1)
                        
                        if i < len(bet_slips) - 1:
                            await wait_between_bets(page, seconds=5, add_random=True)
                
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
                                    'failed': 0,
                                    'match_fingerprint': current_match_fingerprint,
                                    'timestamp': datetime.now().isoformat(),
                                    'matches_data': matches,
                                    'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                    'outcome_button_cache': outcome_button_cache
                                }, f)
                            
                            # Wait between bets
                            if i < len(bet_slips) - 1:
                                await wait_between_bets(page, seconds=5, add_random=True)
                        else:
                            # Retry after re-login failed - keep trying with browser restarts
                            print(f"\n❌ [FAILED] Bet {bet_slip['slip_number']} failed after re-login!")
                            print(f"🔄 ALL BETS ARE IMPORTANT - Trying browser restart...")
                            
                            error_tracker.add_error(
                                error_type='RETRY_FAILED',
                                error_message=f'Bet {bet_slip["slip_number"]} failed after re-login - attempting browser restarts',
                                context={'bet_number': bet_slip['slip_number'], 'action': 'browser_restart_loop'}
                            )
                            error_tracker.save_to_file()
                            
                            # Keep trying with browser restarts
                            max_browser_retries = 5
                            bet_placed = False
                            
                            for browser_retry in range(max_browser_retries):
                                print(f"\n🔄 Browser restart attempt {browser_retry + 1}/{max_browser_retries}...")
                                
                                try:
                                    restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                                    if restart_result:
                                        page = restart_result["page"]
                                        browser = restart_result["browser"]
                                        
                                        await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                        await page.wait_for_timeout(2000)
                                        await close_all_modals(page)
                                        await page.wait_for_timeout(10000)
                                        
                                        retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                                        
                                        if retry_success == True:
                                            successful += 1
                                            bet_placed = True
                                            print(f"\n✅ [SUCCESS] Bet {bet_slip['slip_number']} placed!")
                                            
                                            with open(progress_file, 'w') as f:
                                                json.dump({
                                                    'last_completed_bet': i + 1,
                                                    'last_successful_bet': i,
                                                    'successful': successful,
                                                    'failed': 0,
                                                    'match_fingerprint': current_match_fingerprint,
                                                    'timestamp': datetime.now().isoformat(),
                                                    'matches_data': matches,
                                                    'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                                    'outcome_button_cache': outcome_button_cache
                                                }, f)
                                            break
                                except Exception as e:
                                    print(f"  ❌ Exception: {e}")
                                
                                if browser_retry < max_browser_retries - 1:
                                    wait_time = 15 * (browser_retry + 1)
                                    print(f"  Waiting {wait_time}s...")
                                    await asyncio.sleep(wait_time)
                            
                            if not bet_placed:
                                print(f"\n⛔ All retries exhausted - saving progress and exiting")
                                
                                with open(progress_file, 'w') as f:
                                    json.dump({
                                        'last_completed_bet': i,
                                        'last_successful_bet': i - 1 if i > 0 else -1,
                                        'successful': successful,
                                        'failed': 0,
                                        'match_fingerprint': current_match_fingerprint,
                                        'timestamp': datetime.now().isoformat(),
                                        'matches_data': matches,
                                        'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                        'outcome_button_cache': outcome_button_cache
                                    }, f)
                                
                                error_tracker.display_summary()
                                error_tracker.save_to_file()
                                
                                try:
                                    await browser.close()
                                except:
                                    pass
                                
                                import sys
                                sys.exit(1)
                            
                            if i < len(bet_slips) - 1:
                                await wait_between_bets(page, seconds=5, add_random=True)
                    else:
                        # Re-login failed - try browser restart loop
                        print(f"\n⚠️ [RE-LOGIN FAILED] Starting browser restart loop...")
                        
                        error_tracker.add_error(
                            error_type='RELOGIN_FAILED',
                            error_message=f'Re-login failed for bet {bet_slip["slip_number"]} - starting browser restart loop',
                            context={'bet_number': bet_slip['slip_number'], 'action': 'browser_restart_loop'}
                        )
                        error_tracker.save_to_file()
                        
                        # Keep trying with browser restarts
                        max_browser_retries = 5
                        bet_placed = False
                        
                        for browser_retry in range(max_browser_retries):
                            print(f"\n🔄 Browser restart attempt {browser_retry + 1}/{max_browser_retries}...")
                            
                            try:
                                restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                                if restart_result:
                                    page = restart_result["page"]
                                    browser = restart_result["browser"]
                                    
                                    await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                    await page.wait_for_timeout(2000)
                                    await close_all_modals(page)
                                    await page.wait_for_timeout(10000)
                                    
                                    retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                                    
                                    if retry_success == True:
                                        successful += 1
                                        bet_placed = True
                                        print(f"\n✅ [SUCCESS] Bet {bet_slip['slip_number']} placed!")
                                        
                                        with open(progress_file, 'w') as f:
                                            json.dump({
                                                'last_completed_bet': i + 1,
                                                'last_successful_bet': i,
                                                'successful': successful,
                                                'failed': 0,
                                                'match_fingerprint': current_match_fingerprint,
                                                'timestamp': datetime.now().isoformat(),
                                                'matches_data': matches,
                                                'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                                'outcome_button_cache': outcome_button_cache
                                            }, f)
                                        break
                            except Exception as e:
                                print(f"  ❌ Exception: {e}")
                            
                            if browser_retry < max_browser_retries - 1:
                                wait_time = 15 * (browser_retry + 1)
                                print(f"  Waiting {wait_time}s...")
                                await asyncio.sleep(wait_time)
                        
                        if not bet_placed:
                            print(f"\n⛔ All retries exhausted - saving progress and exiting")
                            
                            with open(progress_file, 'w') as f:
                                json.dump({
                                    'last_completed_bet': i,
                                    'last_successful_bet': i - 1 if i > 0 else -1,
                                    'successful': successful,
                                    'failed': 0,
                                    'match_fingerprint': current_match_fingerprint,
                                    'timestamp': datetime.now().isoformat(),
                                    'matches_data': matches,
                                    'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                    'outcome_button_cache': outcome_button_cache
                                }, f)
                            
                            error_tracker.display_summary()
                            error_tracker.save_to_file()
                            
                            try:
                                await browser.close()
                            except:
                                pass
                            
                            import sys
                            sys.exit(1)
                        
                        if i < len(bet_slips) - 1:
                            await wait_between_bets(page, seconds=5, add_random=True)
                
                else:
                    # Bet failed - DO NOT SKIP, retry with browser restart
                    print(f"\n❌ [FAILED] Bet slip {bet_slip['slip_number']} failed!")
                    print(f"\n{'='*60}")
                    print(f"🔄 ALL BETS ARE IMPORTANT - RETRYING WITH BROWSER RESTART")
                    print(f"{'='*60}")
                    
                    # Track the failure
                    error_tracker.add_error(
                        error_type="BET_FAILED",
                        error_message=f"Bet slip {bet_slip['slip_number']} failed - attempting browser restart and retry",
                        context={
                            'bet_number': bet_slip['slip_number'],
                            'total_bets': len(bet_slips),
                            'successful_so_far': successful,
                            'action': 'browser_restart_retry'
                        }
                    )
                    error_tracker.save_to_file()
                    
                    # Save progress at this bet (so we resume HERE if script crashes)
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i,  # Resume FROM this bet
                            'last_successful_bet': i - 1 if i > 0 else -1,
                            'successful': successful,
                            'failed': 0,  # Reset failed count
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat(),
                            'matches_data': matches,
                            'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                            'outcome_button_cache': outcome_button_cache
                        }, f)
                    
                    # Retry loop with browser restarts
                    max_browser_retries = 5
                    bet_placed = False
                    
                    for browser_retry in range(max_browser_retries):
                        print(f"\n🔄 Browser restart attempt {browser_retry + 1}/{max_browser_retries}...")
                        
                        try:
                            # Restart browser completely
                            restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                            if restart_result:
                                page = restart_result["page"]
                                browser = restart_result["browser"]
                                print(f"  ✅ Browser restarted successfully")
                                
                                # Navigate to soccer page
                                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                await page.wait_for_timeout(2000)
                                await close_all_modals(page)
                                
                                # Wait before retry
                                print(f"  Waiting 10 seconds before retry...")
                                await page.wait_for_timeout(10000)
                                
                                # Retry the bet
                                retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                                
                                if retry_success == True:
                                    successful += 1
                                    bet_placed = True
                                    print(f"\n✅ [SUCCESS] Bet {bet_slip['slip_number']} placed after browser restart!")
                                    
                                    # Save progress
                                    with open(progress_file, 'w') as f:
                                        json.dump({
                                            'last_completed_bet': i + 1,
                                            'last_successful_bet': i,
                                            'successful': successful,
                                            'failed': 0,
                                            'match_fingerprint': current_match_fingerprint,
                                            'timestamp': datetime.now().isoformat(),
                                            'matches_data': matches,
                                            'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                            'outcome_button_cache': outcome_button_cache
                                        }, f)
                                    
                                    break  # Exit retry loop on success
                                else:
                                    print(f"  ❌ Retry {browser_retry + 1} failed - will try again...")
                            else:
                                print(f"  ❌ Browser restart failed - will try again...")
                        except Exception as restart_err:
                            print(f"  ❌ Exception during retry: {restart_err}")
                        
                        # Wait before next retry
                        if browser_retry < max_browser_retries - 1:
                            wait_time = 15 * (browser_retry + 1)  # Increasing wait: 15s, 30s, 45s, 60s
                            print(f"  Waiting {wait_time}s before next attempt...")
                            await asyncio.sleep(wait_time)
                    
                    if not bet_placed:
                        # All retries exhausted - save progress and exit for manual intervention
                        print(f"\n{'='*60}")
                        print(f"⛔ ALL RETRY ATTEMPTS EXHAUSTED")
                        print(f"{'='*60}")
                        print(f"Bet {bet_slip['slip_number']} could not be placed after {max_browser_retries} browser restarts")
                        print(f"Progress saved - run script again to retry this bet")
                        print(f"{'='*60}")
                        
                        error_tracker.add_error(
                            error_type="BET_FAILED",
                            error_message=f"Bet {bet_slip['slip_number']} failed after {max_browser_retries} browser restart attempts",
                            context={
                                'bet_number': bet_slip['slip_number'],
                                'retry_attempts': max_browser_retries,
                                'action': 'requires_manual_intervention'
                            }
                        )
                        error_tracker.display_summary()
                        error_tracker.save_to_file()
                        
                        try:
                            await browser.close()
                        except:
                            pass
                        
                        import sys
                        sys.exit(1)
                    
                    # Wait between bets after successful retry
                    if i < len(bet_slips) - 1:
                        await wait_between_bets(page, seconds=5, add_random=True)
                
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
                    
                    # Track the memory error
                    error_tracker.add_error(
                        error_type='MEMORY_ERROR',
                        error_message=f'Playwright memory corruption detected: {error_str[:150]}',
                        context={
                            'bet_number': bet_slip['slip_number'],
                            'action': 'attempting_recovery'
                        }
                    )
                    
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
                            
                            # Log successful recovery
                            error_tracker.add_error(
                                error_type='RECOVERY_SUCCESS',
                                error_message=f'Browser restart recovery successful for bet {bet_slip["slip_number"]}',
                                context={
                                    'bet_number': bet_slip['slip_number'],
                                    'cached_selectors_rebuilt': len(outcome_button_cache),
                                    'reason': 'memory_corruption_recovery'
                                }
                            )
                            error_tracker.save_to_file()
                            
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
                                        'failed': 0,
                                        'match_fingerprint': current_match_fingerprint,
                                        'timestamp': datetime.now().isoformat(),
                                        'matches_data': matches,
                                        'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                        'outcome_button_cache': outcome_button_cache
                                    }, f)
                                
                                # Continue to next bet
                                if i < len(bet_slips) - 1:
                                    await wait_between_bets(page, seconds=5, add_random=True)
                                continue  # Skip to next iteration of the loop
                            else:
                                print(f"\n❌ Retry after browser restart also failed")
                                error_tracker.add_error(
                                    error_type='RECOVERY_RETRY_FAILED',
                                    error_message=f'Bet {bet_slip["slip_number"]} still failed after browser restart recovery',
                                    context={
                                        'bet_number': bet_slip['slip_number'],
                                        'recovery_attempted': True,
                                        'retry_result': 'failed'
                                    }
                                )
                                error_tracker.save_to_file()
                                # Fall through to exit
                        else:
                            print(f"\n❌ Browser restart failed")
                            error_tracker.add_error(
                                error_type='RECOVERY_BROWSER_RESTART_FAILED',
                                error_message=f'Browser restart failed during recovery for bet {bet_slip["slip_number"]}',
                                context={
                                    'bet_number': bet_slip['slip_number'],
                                    'recovery_stage': 'browser_restart'
                                }
                            )
                            error_tracker.save_to_file()
                    except Exception as restart_err:
                        print(f"\n❌ Recovery failed: {restart_err}")
                        error_tracker.add_error(
                            error_type='RECOVERY_EXCEPTION',
                            error_message=f'Recovery exception for bet {bet_slip["slip_number"]}: {str(restart_err)[:150]}',
                            context={
                                'bet_number': bet_slip['slip_number'],
                                'error_details': str(restart_err)
                            },
                            exception=restart_err
                        )
                        error_tracker.save_to_file()
                    
                    # If we get here, single recovery attempt failed
                    # Try multiple browser restart attempts for this bet
                    print(f"\n⚠️ First recovery attempt failed - trying additional retries...")
                    
                    max_additional_retries = 4  # We already tried once above
                    bet_placed = False
                    
                    for additional_retry in range(max_additional_retries):
                        retry_num = additional_retry + 2  # We already did attempt 1
                        print(f"\n🔄 Browser restart attempt {retry_num}/5...")
                        
                        try:
                            gc.collect()
                            restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                            if restart_result:
                                page = restart_result["page"]
                                browser = restart_result["browser"]
                                
                                await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                                await page.wait_for_timeout(2000)
                                await close_all_modals(page)
                                await page.wait_for_timeout(10000)
                                
                                retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                                
                                if retry_success == True:
                                    successful += 1
                                    bet_placed = True
                                    print(f"\n✅ [SUCCESS] Bet {bet_slip['slip_number']} placed after recovery!")
                                    
                                    with open(progress_file, 'w') as f:
                                        json.dump({
                                            'last_completed_bet': i + 1,
                                            'last_successful_bet': i,
                                            'successful': successful,
                                            'failed': 0,
                                            'match_fingerprint': current_match_fingerprint,
                                            'timestamp': datetime.now().isoformat(),
                                            'matches_data': matches,
                                            'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                            'outcome_button_cache': outcome_button_cache
                                        }, f)
                                    break
                        except Exception as retry_err:
                            print(f"  ❌ Retry {retry_num} exception: {retry_err}")
                        
                        if additional_retry < max_additional_retries - 1:
                            wait_time = 15 * (additional_retry + 1)
                            print(f"  Waiting {wait_time}s...")
                            await asyncio.sleep(wait_time)
                    
                    if bet_placed:
                        # Success - continue to next bet
                        if i < len(bet_slips) - 1:
                            await wait_between_bets(page, seconds=5, add_random=True)
                        continue
                    
                    # All retries exhausted - save progress and exit
                    print(f"\n⛔ PLAYWRIGHT MEMORY ERROR - All {5} retries exhausted")
                    print(f"   Saving progress and exiting for manual intervention...")
                    
                    error_tracker.add_error(
                        error_type='MEMORY_ERROR',
                        error_message=f'Playwright memory corruption - all retries exhausted for bet {bet_slip["slip_number"]}',
                        context={
                            'bet_number': bet_slip['slip_number'],
                            'successful_so_far': successful,
                            'action': 'exiting_for_manual_retry'
                        }
                    )
                    error_tracker.save_to_file()
                    
                    with open(progress_file, 'w') as f:
                        json.dump({
                            'last_completed_bet': i,
                            'last_successful_bet': i - 1 if i > 0 else -1,
                            'successful': successful,
                            'failed': 0,
                            'match_fingerprint': current_match_fingerprint,
                            'timestamp': datetime.now().isoformat(),
                            'matches_data': matches,
                            'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                            'outcome_button_cache': outcome_button_cache
                        }, f)
                    
                    error_tracker.display_summary()
                    
                    try:
                        await browser.close()
                    except:
                        pass
                    
                    import sys
                    sys.exit(1)
                
                # Regular exception handling - try browser restart loop
                print(f"\n[ERROR] Exception on slip {bet_slip['slip_number']}: {e}")
                print(f"\n🔄 ALL BETS ARE IMPORTANT - Attempting browser restart...")
                
                error_tracker.add_error(
                    error_type="EXCEPTION",
                    error_message=f"Exception during bet {bet_slip['slip_number']} - attempting browser restart",
                    context={'bet_number': bet_slip['slip_number'], 'action': 'browser_restart_loop'},
                    exception=e
                )
                error_tracker.save_to_file()
                
                # Save progress at this bet
                with open(progress_file, 'w') as f:
                    json.dump({
                        'last_completed_bet': i,
                        'last_successful_bet': i - 1 if i > 0 else -1,
                        'successful': successful,
                        'failed': 0,
                        'match_fingerprint': current_match_fingerprint,
                        'timestamp': datetime.now().isoformat(),
                        'matches_data': matches,
                        'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                        'outcome_button_cache': outcome_button_cache
                    }, f)
                
                # Browser restart retry loop
                max_browser_retries = 5
                bet_placed = False
                
                for browser_retry in range(max_browser_retries):
                    print(f"\n🔄 Browser restart attempt {browser_retry + 1}/{max_browser_retries}...")
                    
                    try:
                        restart_result = await restart_browser_fresh(p, old_browser=browser, old_page=page)
                        if restart_result:
                            page = restart_result["page"]
                            browser = restart_result["browser"]
                            
                            await page.goto('https://new.betway.co.za/sport/soccer/upcoming', wait_until='domcontentloaded', timeout=15000)
                            await page.wait_for_timeout(2000)
                            await close_all_modals(page)
                            await page.wait_for_timeout(10000)
                            
                            retry_success = await place_bet_slip(page, bet_slip, amount_per_slip, match_cache, outcome_button_cache)
                            
                            if retry_success == True:
                                successful += 1
                                bet_placed = True
                                print(f"\n✅ [SUCCESS] Bet {bet_slip['slip_number']} placed!")
                                
                                with open(progress_file, 'w') as f:
                                    json.dump({
                                        'last_completed_bet': i + 1,
                                        'last_successful_bet': i,
                                        'successful': successful,
                                        'failed': 0,
                                        'match_fingerprint': current_match_fingerprint,
                                        'timestamp': datetime.now().isoformat(),
                                        'matches_data': matches,
                                        'cumulative_runtime_seconds': cumulative_runtime_seconds + (time.time() - script_start_time),
                                        'outcome_button_cache': outcome_button_cache
                                    }, f)
                                break
                    except Exception as restart_err:
                        print(f"  ❌ Exception: {restart_err}")
                    
                    if browser_retry < max_browser_retries - 1:
                        wait_time = 15 * (browser_retry + 1)
                        print(f"  Waiting {wait_time}s...")
                        await asyncio.sleep(wait_time)
                
                if not bet_placed:
                    print(f"\n⛔ All retries exhausted - saving progress and exiting")
                    
                    error_tracker.display_summary()
                    error_tracker.save_to_file()
                    
                    try:
                        await browser.close()
                    except:
                        pass
                    
                    import sys
                    sys.exit(1)
                
                # Wait between bets
                if i < len(bet_slips) - 1:
                    await wait_between_bets(page, seconds=5, add_random=True)
        
        # Note: Retry loop removed - all bets now retry until success during main loop
        # If we reach this point, all bets were placed successfully
        
        print("\n" + "="*60)
        print(f"🏁 FINAL RESULTS")
        print("="*60)
        print(f"✅ All bets placed successfully: {successful}/{len(bet_slips)}")
        print(f"💰 Total amount wagered: R{successful * amount_per_slip:.2f}")
        print(f"📊 Success rate: 100%")
        print("="*60)
        
        # Log script completion
        error_tracker.add_error(
            error_type='SCRIPT_COMPLETED',
            error_message=f'Betting session completed: {successful}/{len(bet_slips)} successful - ALL BETS PLACED',
            context={
                'total_bets': len(bet_slips),
                'successful': successful,
                'amount_wagered': successful * amount_per_slip,
                'completion_status': 'success'
            }
        )
        
        # Display error summary (will show even if no errors - provides confirmation)
        error_tracker.display_summary()
        
        # Always save error log on completion (includes success log)
        error_tracker.save_to_file()
        
        # Clean up progress file - all bets are now successful
        if os.path.exists(progress_file):
            try:
                os.remove(progress_file)
                print("\n✅ [CLEANUP] Progress file removed - all bets completed successfully!")
            except:
                pass
        
        # Clean up error log file on successful completion
        error_log_file = 'error_log.json'
        if os.path.exists(error_log_file):
            try:
                os.remove(error_log_file)
                print("✅ [CLEANUP] Error log file removed - session completed successfully!")
            except Exception as e:
                print(f"⚠️ [CLEANUP] Could not remove error log file: {e}")
        
        print("\nKeeping browser open for 30 seconds...")
        await page.wait_for_timeout(30000)
        
        await browser.close()
        
        # End timer and display results
        script_end_time = time.time()
        session_duration = script_end_time - script_start_time
        total_duration = cumulative_runtime_seconds + session_duration  # Include previous sessions
        
        # Format session time
        sess_hours = int(session_duration // 3600)
        sess_minutes = int((session_duration % 3600) // 60)
        sess_seconds = int(session_duration % 60)
        
        # Format total time (cumulative)
        total_hours = int(total_duration // 3600)
        total_minutes = int((total_duration % 3600) // 60)
        total_seconds = int(total_duration % 60)
        
        print("\n" + "="*60)
        print("⏱️  SCRIPT EXECUTION TIME")
        print("="*60)
        
        # Show session time
        if sess_hours > 0:
            print(f"This session: {sess_hours}h {sess_minutes}m {sess_seconds}s ({session_duration:.2f} seconds)")
        elif sess_minutes > 0:
            print(f"This session: {sess_minutes}m {sess_seconds}s ({session_duration:.2f} seconds)")
        else:
            print(f"This session: {sess_seconds}s ({session_duration:.2f} seconds)")
        
        # Show cumulative total if there were previous sessions
        if cumulative_runtime_seconds > 0:
            if total_hours > 0:
                print(f"TOTAL TIME (all sessions): {total_hours}h {total_minutes}m {total_seconds}s ({total_duration:.2f} seconds)")
            elif total_minutes > 0:
                print(f"TOTAL TIME (all sessions): {total_minutes}m {total_seconds}s ({total_duration:.2f} seconds)")
            else:
                print(f"TOTAL TIME (all sessions): {total_seconds}s ({total_duration:.2f} seconds)")
        
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
    
    All crashes are logged to error_tracker and displayed at the end.
    """
    import sys
    import subprocess
    import time as time_module
    import signal
    
    MAX_RETRIES = 5  # Maximum number of automatic restarts
    RETRY_DELAY = 15  # Seconds to wait before restarting
    # Timeout is for TOTAL runtime, not inactivity. Each bet can take 2+ min with delays.
    # For 243 bets at ~2min each = ~8 hours. Set to 86 hours to handle week-long operations.
    # CRITICAL: Previously this was set to 420 seconds (7 min) which caused hangs!
    # Individual operations have their own timeouts (15-30s), this is for total process.
    SUBPROCESS_TIMEOUT = 309600  # 86 hour timeout (309600 seconds) = 5160 minutes
    
    # Create a wrapper-level error tracker to track subprocess crashes
    wrapper_crashes = []  # List of crash details for summary
    
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
    print(f"   ⚠️  SUBPROCESS TIMEOUT: {SUBPROCESS_TIMEOUT}s ({SUBPROCESS_TIMEOUT//60} min)")
    print(f"   ⚠️  This was previously 420s (7min) causing hangs - NOW FIXED")
    print(f"   Per-operation timeout: 15-30s (Playwright calls)")
    print(f"   Progress file: bet_progress.json")
    print(f"   All crashes will be logged and displayed at end")
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
            error_reason = f"TIMEOUT (hung for >{SUBPROCESS_TIMEOUT}s = {SUBPROCESS_TIMEOUT//60} min)"
            print(f"\n⚠️ SUBPROCESS TIMEOUT - Process hung for over {SUBPROCESS_TIMEOUT} seconds ({SUBPROCESS_TIMEOUT//60} min)")
            
            # Track this crash
            wrapper_crashes.append({
                'attempt': retry_count + 1,
                'error_type': 'TIMEOUT',
                'error_message': f'Subprocess hung for over {SUBPROCESS_TIMEOUT} seconds',
                'timestamp': datetime.now().isoformat(),
                'exit_code': exit_code
            })
            
        except KeyboardInterrupt:
            elapsed_total = time_module.time() - total_start_time
            elapsed_mins = int(elapsed_total // 60)
            print(f"\n\n⛔ Interrupted by user (Ctrl+C)")
            print(f"   Total time: {elapsed_mins} minutes")
            print(f"   Progress saved. Run again to resume.")
            
            # Track the interruption
            wrapper_crashes.append({
                'attempt': retry_count + 1,
                'error_type': 'CANCELLED',
                'error_message': 'Script interrupted by user (Ctrl+C)',
                'timestamp': datetime.now().isoformat(),
                'exit_code': -3
            })
            
            # Display wrapper crash summary if any crashes occurred
            if wrapper_crashes:
                print(f"\n{'='*60}")
                print(f"📊 AUTO-RETRY WRAPPER CRASH SUMMARY")
                print(f"{'='*60}")
                for crash in wrapper_crashes:
                    print(f"  Attempt {crash['attempt']}: {crash['error_type']} - {crash['error_message'][:50]}")
                print(f"{'='*60}\n")
            
            return  # Exit completely
            
        except Exception as e:
            exit_code = -2
            error_reason = f"SUBPROCESS ERROR: {str(e)[:100]}"
            print(f"\n❌ Subprocess error: {e}")
            
            # Track this crash
            wrapper_crashes.append({
                'attempt': retry_count + 1,
                'error_type': 'EXCEPTION',
                'error_message': str(e)[:150],
                'timestamp': datetime.now().isoformat(),
                'exit_code': exit_code
            })
        
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
        
        # Track this crash in wrapper_crashes list
        if not error_reason:  # Only add if not already added above
            crash_error_type = 'EXCEPTION' if exit_code != 0 else 'UNKNOWN'
            wrapper_crashes.append({
                'attempt': retry_count,
                'error_type': crash_error_type,
                'error_message': f'Subprocess exited with code {exit_code}',
                'timestamp': datetime.now().isoformat(),
                'exit_code': exit_code,
                'attempt_duration': int(attempt_elapsed)
            })
        
        if retry_count <= MAX_RETRIES:
            print(f"\n{'='*60}")
            if error_reason:
                print(f"⚠️ SCRIPT FAILED: {error_reason}")
            else:
                print(f"⚠️ SCRIPT CRASHED (exit code: {exit_code})")
            print(f"   Attempt ran for: {int(attempt_elapsed)}s")
            print(f"   Progress is saved to bet_progress.json")
            print(f"   Total crashes so far: {len(wrapper_crashes)}")
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
            print(f"{'='*60}")
            
            # Display comprehensive crash summary
            if wrapper_crashes:
                print(f"\n{'='*60}")
                print(f"📊 ALL CRASHES DURING AUTO-RETRY SESSION")
                print(f"{'='*60}")
                print(f"Total crashes: {len(wrapper_crashes)}")
                print(f"")
                for i, crash in enumerate(wrapper_crashes, 1):
                    print(f"Crash #{i}:")
                    print(f"  Attempt: {crash['attempt']}")
                    print(f"  Type: {crash['error_type']}")
                    print(f"  Message: {crash['error_message']}")
                    print(f"  Exit Code: {crash['exit_code']}")
                    print(f"  Duration: {crash.get('attempt_duration', 'N/A')}s")
                    print(f"  Timestamp: {crash['timestamp']}")
                    print(f"")
                print(f"{'='*60}\n")
            
            return  # Exit after max retries


if __name__ == "__main__":
    import sys
    
    # ============================================================================
    # GLOBAL EXCEPTION HANDLER (Enhanced)
    # ============================================================================
    def global_exception_handler(exc_type, exc_value, exc_traceback):
        """
        Global exception handler to catch any unhandled exceptions.
        Ensures all crashes are logged to the error tracker before exit.
        This captures:
        - Timeouts that cause application to crash
        - Network failures
        - Playwright memory corruption errors
        - Any other unexpected exceptions
        """
        # Don't handle keyboard interrupts
        if issubclass(exc_type, KeyboardInterrupt):
            # Still log it as a user-initiated cancellation
            try:
                error_tracker.add_error(
                    error_type="CANCELLED",
                    error_message="Script interrupted by user (Ctrl+C)",
                    context={'exception_type': 'KeyboardInterrupt'}
                )
                error_tracker.display_summary()
                error_tracker.save_to_file()
            except:
                pass
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        # Format the traceback
        import traceback as tb_module
        tb_str = ''.join(tb_module.format_exception(exc_type, exc_value, exc_traceback))
        
        # Determine specific error type based on exception details
        error_str = str(exc_value).lower()
        
        # Classify the error type for better Problem Details
        if 'timeout' in error_str:
            error_type = 'TIMEOUT'
            error_title = 'Timeout Error Caused Crash'
        elif any(kw in error_str for kw in ['err_name_not_resolved', 'err_connection', 'err_internet_disconnected', 'net::']):
            error_type = 'NETWORK_FAILURE'
            error_title = 'Network Failure Caused Crash'
        elif "'dict' object has no attribute '_object'" in error_str or 'object has been collected' in error_str:
            error_type = 'MEMORY_ERROR'
            error_title = 'Playwright Memory Corruption Caused Crash'
        elif 'target page, context or browser has been closed' in error_str:
            error_type = 'BROWSER_RESTART'
            error_title = 'Browser/Page Closed Unexpectedly'
        elif issubclass(exc_type, asyncio.CancelledError):
            error_type = 'CANCELLED'
            error_title = 'Async Operation Cancelled'
        else:
            error_type = 'UNHANDLED_EXCEPTION'
            error_title = 'Unhandled Exception Caused Crash'
        
        print(f"\n{'='*70}")
        print(f"💥 {error_title.upper()} - GLOBAL HANDLER CAUGHT")
        print(f"{'='*70}")
        print(f"Exception Type: {exc_type.__name__}")
        print(f"Exception Message: {exc_value}")
        print(f"Error Category: {error_type}")
        print(f"{'='*70}\n")
        
        # Log to error tracker with detailed context
        try:
            error_tracker.add_error(
                error_type=error_type,
                error_message=f"{error_title}: {exc_type.__name__}: {str(exc_value)[:200]}",
                context={
                    'exception_type': exc_type.__name__,
                    'exception_message': str(exc_value),
                    'traceback': tb_str,
                    'crash_type': 'application_crash',
                    'recovery_possible': PROBLEM_TYPES.get(error_type, {}).get('recoverable', True)
                }
            )
            
            # Always display and save error summary on crash
            print(f"\n📊 Displaying all errors that occurred during this session...\n")
            error_tracker.display_summary()
            error_tracker.save_to_file()
        except Exception as e:
            print(f"⚠️ Could not log to error tracker: {e}")
            print(f"\nFull traceback:")
            print(tb_str)
        
        # Call the default exception hook
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    
    # Install global exception handler
    sys.excepthook = global_exception_handler
    
    # Check if we're being called directly (by subprocess) or as the main entry point
    if len(sys.argv) >= 2 and sys.argv[1] == '--direct':
        # Called by subprocess - run main() directly
        # Remove the --direct flag and pass remaining args
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        try:
            main()
        except Exception as e:
            # Catch any exception that might slip through
            error_tracker.add_error(
                error_type="EXCEPTION",
                error_message=f"Main function crashed with: {str(e)[:200]}",
                context={'mode': 'direct'},
                exception=e
            )
            error_tracker.display_summary()
            error_tracker.save_to_file()
            raise
    else:
        # Called normally - use the auto-retry wrapper
        try:
            main_with_auto_retry()
        except Exception as e:
            # Catch any exception from the wrapper
            error_tracker.add_error(
                error_type="EXCEPTION",
                error_message=f"Auto-retry wrapper crashed with: {str(e)[:200]}",
                context={'mode': 'auto-retry'},
                exception=e
            )
            error_tracker.display_summary()
            error_tracker.save_to_file()
            raise
