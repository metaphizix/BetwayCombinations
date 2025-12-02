# Betway Betting Automation

Automated betting system for Betway that creates all possible outcome combinations for multiple matches.

## How It Works

### 1. Login

- Uses credentials from `.env` file
- Automatically logs in to Betway
- Verifies successful login by checking balance
- Includes network retry mechanism with exponential backoff (3 retries: 5s → 10s → 20s)
- Multiple login button selectors for reliability

### 2. User Input

**Interactive Mode** (when run without arguments):
User is prompted for:

- **Matches per Slip**: How many matches each bet slip should contain (e.g., 2)
- **Amount per Slip**: How much to bet on each slip in Rands (e.g., 1.0)
- System shows total slips and cost before confirmation at each step

**CLI Mode** (automated):

```bash
python main.py <num_matches> <amount_per_slip>
# Example: python main.py 2 1.0
```

- Skips interactive prompts
- Useful for automated testing or scheduling

### 3. Automatic Match Selection

The system automatically:

- Navigates to upcoming soccer matches (Premier League - Full-Time Result market)
- **Single Scraping Pass**: Scrapes up to 20 pages ONCE at the beginning
- **URL Caching**: Captures and stores match URLs for every match during the single scraping pass
- **Offline Filtering**: Filters scraped matches to find those meeting requirements (no additional scraping)
- **URL Validation**: Verifies all selected matches have cached URLs before allowing any bets
- Filters matches that start 3.5+ hours from now
- Ensures minimum 2.5-hour gaps between matches
- Validates time requirements before proceeding
- User selects the desired number of matches (Interactive Mode only)

### 4. Combination Generation

**Important**: Each bet slip contains ALL the matches you specify, not just one match.

**Example with 3 matches:**

- If you choose 3 matches, each bet slip will have all 3 matches
- The system creates all possible outcome combinations across those 3 matches
- Total combinations = 3^3 = 27 bet slips
- Each slip has 3 matches with different outcome predictions

**Bet Slip Examples:**

```
Slip 1:  Match1=1, Match2=1, Match3=1
Slip 2:  Match1=1, Match2=1, Match3=X
Slip 3:  Match1=1, Match2=1, Match3=2
Slip 4:  Match1=1, Match2=X, Match3=1
...
Slip 27: Match1=2, Match2=2, Match3=2
```

### 5. Cost Calculation

Before placing any bets, the system shows:

- Total number of bet slips that will be created
- Cost per slip
- **Total cost** = (Number of slips) × (Amount per slip)

**Example:**

- 3 matches = 27 combinations
- R10 per slip
- Total cost = R270

### 6. Confirmation

You must confirm before any bets are placed:

1. After entering number of matches (shows total slips)
2. After entering bet amount (shows total cost)
3. Betway's confirmation popup (automated during bet placement)

### 7. Bet Placement

**⚠️ IMPORTANT: Scraping happens ONCE, betting uses cached URLs!**

The system workflow:

1. **Scraping Phase (happens once)**:

   - Scrapes up to 20 pages to collect ALL available matches
   - Captures URL for each match by clicking and navigating to match page
   - All match data and URLs cached in memory
   - No additional scraping after this phase

2. **Filtering Phase**:

   - Filters the scraped matches based on time and gap requirements
   - Works entirely from cached data (no scraping)
   - Selects required number of matches

3. **Validation Phase**:

   - Validates all selected matches have cached URLs
   - **Fails immediately if any match is missing a URL** (prevents partial bet placement)
   - Verifies time gaps between matches

4. **Combination Generation**:

   - Generates all possible bet combinations (3^n where n = number of matches)

5. **Betting Phase** (uses only cached URLs):

   - For each bet slip:
     - Clear the betslip
     - **Navigate directly to each match using cached URL** (instant - no searching)
     - Click the predicted outcome for all matches in the combination
     - Enter the bet amount
     - Click "Bet Now" button (using container-scoped selectors)
     - **5 Click Methods**: Tries multiple click approaches to ensure button is clicked
     - Verify confirmation modal appears ("Bet Confirmation")
     - Click "Continue betting" to proceed

6. **Progress Tracking**: Saves to `bet_progress.json` after each bet for resume capability

7. **Network Retry**: Automatic retry on connection errors (exponential backoff: 5s → 10s → 20s)

8. **Anti-Detection Features** (preserved from original):

   - Random delays between bets (5s base + 10-60s random)
   - Browser restart every 5 bets
   - Varying wait times
   - Human-like behavior patterns

9. **Termination on Failure**: If any bet fails, the entire application stops immediately

## Setup

1. Install the required packages:

```powershell
pip install -r requirements.txt
```

2. Install Playwright browsers:

```powershell
playwright install chromium
```

3. Create a `.env` file with your credentials:

```
BETWAY_USERNAME=your_username
BETWAY_PASSWORD=your_password
```

## Files

- `main.py` - Complete betting automation (login, match selection, bet placement)
- `.env` - Credentials (username and password)
- `requirements.txt` - Python dependencies
- `bet_progress.json` - Auto-generated progress tracking (allows resume on failure)
- `RETRY_MECHANISM.md` - Documentation for network retry features

## Usage

### Interactive Mode

```bash
python main.py
```

This will:

1. Login automatically using `.env` credentials
2. Prompt you for number of matches per slip
3. Show total bet slips that will be created
4. Prompt you for amount per slip
5. Show total cost
6. Ask for confirmation at each step
7. Automatically find suitable matches (3.5+ hours away, 2.5+ hour gaps)
8. Place all bet combinations
9. Track progress in `bet_progress.json`
10. Auto-resume if interrupted

### CLI Mode (Automated)

```bash
python main.py <num_matches> <amount_per_slip>
```

Example:

```bash
python main.py 2 1.0
```

This will:

1. Login automatically using `.env` credentials
2. Skip interactive prompts (uses provided arguments)
3. Automatically find suitable matches
4. Place all bet combinations (3^2 = 9 slips at R1.0 each)
5. Track progress in `bet_progress.json`
6. Auto-resume if interrupted

## Key Features

### Network Resilience

- **Exponential backoff retry** - Automatically retries on network errors (5s → 10s → 20s)
- **Progress tracking** - Saves progress after each bet in `bet_progress.json`
- **Auto-resume** - Continues from last successful bet if script restarts
- **Browser recovery** - Restarts browser and reconnects on failures

### Anti-Detection

- **Random delays** - Adds 10-60 second random waits between bets
- **Browser restarts** - Clears tracking data every 5 bets
- **Variable timing** - Mimics human-like behavior

### Smart Match Selection

- **Time-based filtering** - Only matches starting 3.5+ hours from now
- **Gap validation** - Ensures 2.5+ hour gaps between matches
- **Pagination support** - Automatically searches up to 20 pages
- **URL caching** - Captures match URLs during scraping for direct navigation
- **Runtime validation** - Double-checks time requirements before betting

### Performance Optimizations

- **Single scraping pass** - Scrapes up to 20 pages ONCE at the beginning
- **Offline filtering** - All filtering happens on cached data (no additional scraping)
- **Direct URL navigation** - All bets use cached URLs from the single scraping pass
- **No searching during bets** - Eliminates time-consuming page searches during bet placement
- **Optimized wait times** - Reduced by ~50-60% while maintaining reliability
- **URL validation** - Fails immediately if any match URL was not captured during scraping

### Bet Placement

- **Container-scoped selectors** - Targets buttons within specific betslip containers
- **5 Click Methods** - Multiple approaches to ensure button clicks (Method 2: Direct Playwright click proven most reliable)
- **Modal Verification** - Confirms "Bet Confirmation" modal appears after each click
- **DOM Re-querying** - Handles element detachment by re-querying before each click attempt
- **Retry on failure** - Retries failed bets with fresh browser session
- **Detailed logging** - Shows progress for every action
- **Immediate termination** - Stops completely if any bet fails

## Important Notes

1. **Each bet slip is a multi-match bet** - Contains ALL selected matches with different outcome combinations
2. **Total cost grows exponentially** - 3 matches = 27 slips, 4 matches = 81 slips, 5 matches = 243 slips!
3. **Always confirm** - Check the total cost before confirming (you'll be asked twice in Interactive Mode)
4. **Watch the first few bets** - Verify they're placing correctly
5. **Don't close the browser** - The automation controls the browser window
6. **Resume capability** - If interrupted, run again to continue from last successful bet
7. **Match timing** - Only selects matches starting 3.5+ hours away with 2.5+ hour gaps
8. **Pagination** - Automatically clicks "Next" button to load more matches as needed
9. **Termination on failure** - Application stops completely if any bet fails (check logs for details)

## Calculation Table

| Matches | Total Slips | Cost @ R10/slip | Cost @ R5/slip |
| ------- | ----------- | --------------- | -------------- |
| 2       | 9           | R90             | R45            |
| 3       | 27          | R270            | R135           |
| 4       | 81          | R810            | R405           |
| 5       | 243         | R2,430          | R1,215         |
| 6       | 729         | R7,290          | R3,645         |

## Troubleshooting

### Bets not placing

- The system will automatically retry on network errors (up to 3 times with exponential backoff)
- Check console logs for detailed error messages showing which click method succeeded
- Verify sufficient balance in Betway account
- Try with smaller amount first (R1.00)
- Check `bet_progress.json` to see which bets succeeded
- If "Bet Now" button not clicking, the system tries 5 different click methods automatically

### Network errors (ERR_NAME_NOT_RESOLVED, timeouts)

- System automatically retries with exponential backoff (5s → 10s → 20s, max 3 attempts)
- If it fails after retries, run the script again to resume
- Progress is saved after each successful bet
- Browser will restart and reconnect automatically

### Login fails

- Verify `.env` file has correct `BETWAY_USERNAME` and `BETWAY_PASSWORD`
- Check internet connection
- Try manual login first to ensure credentials work
- System will retry login up to 3 times automatically

### Not enough matches found

- System searches up to 20 pages using pagination
- Automatically clicks "Next" button to load more matches
- Captures and caches match URLs during scraping
- Looks for matches starting 3.5+ hours away with 2.5+ hour gaps
- Try during peak hours (more upcoming matches available)
- Try with fewer matches (2 instead of 3)
- Check that upcoming soccer matches exist on Betway

### Match not found during bet placement

- All bets use cached URLs from the initial scraping phase
- **URL validation runs before any bets are placed** - ensures all matches have valid URLs
- If a match URL was not captured during scraping, the system stops before placing ANY bets
- This prevents partial bet placement and ensures all-or-nothing approach
- System will log detailed error showing which matches are missing URLs
