"""
Polymarket Survival Bot
An autonomous AI trading bot for Polymarket prediction markets

Usage:
    python bot.py              # Run one trading cycle
    python bot.py --dry-run    # Run without placing real bets
"""

import csv
import json
import argparse
from datetime import datetime, date
from typing import List, Tuple
import anthropic

import config
from markets import get_markets_list, get_market_detail
from account import AccountService
from research import get_research_result, research_market_and_save
from trading import PolymarketTrader


# =============================================================================
# Tool Definitions for Claude
# =============================================================================

TOOLS = [
    # =========================================================================
    # 1. Market Information (Read-only, Free)
    # =========================================================================
    {
        "name": "get_markets_list",
        "description": """Get active markets list from Polymarket.
Returns non-sports, non-price-prediction events.
Each market includes: id, title, category, price (YES price), volume_24h, liquidity, end_date.
Default returns top 50 markets sorted by liquidity.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of markets to return, default 50, max 100"
                },
                "min_liquidity": {
                    "type": "number",
                    "description": "Minimum liquidity in USD, default 5000"
                },
                "category": {
                    "type": "string",
                    "description": "Category filter: politics, crypto, science, entertainment, business"
                }
            },
            "required": []
        }
    },
    
    {
        "name": "get_market_detail",
        "description": """Get detailed information for a single market.
Returns: id, title, description, rules (resolution rules), price, volume, liquidity,
end_date, created_date, outcomes (YES/NO details).
Use this to understand market rules before betting.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {
                    "type": "string",
                    "description": "Market ID"
                }
            },
            "required": ["market_id"]
        }
    },

    # =========================================================================
    # 2. Research (get is free, research costs money)
    # =========================================================================
    {
        "name": "get_research_result",
        "description": """Get cached research result for a market.
Returns: market_id, research_time, summary, estimated_probability, confidence, key_factors, sources.
Returns null if not researched yet.
⚡ FREE - Always check cache first before researching.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {
                    "type": "string",
                    "description": "Market ID"
                }
            },
            "required": ["market_id"]
        }
    },
    
    {
        "name": "research_market_and_save",
        "description": """Deep research a market using web search, analyze and save results.
Automatically searches the web and synthesizes findings.
Returns: summary, estimated_probability (your estimate), confidence (0-1), key_factors, sources.
⚠️ COST: ~$0.05/call - Check cache with get_research_result first!
Maximum 5 research calls per cycle.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {
                    "type": "string",
                    "description": "Market ID"
                },
                "market_title": {
                    "type": "string",
                    "description": "Market title for search"
                },
                "focus": {
                    "type": "string",
                    "description": "Research focus, e.g., 'focus on recent polls', 'look for official statements'"
                }
            },
            "required": ["market_id", "market_title"]
        }
    },

    # =========================================================================
    # 3. Account Information (Read-only, Free)
    # =========================================================================
    {
        "name": "get_balance",
        "description": """Get current account balance.
Returns: available_usdc (available for betting), locked_usdc (in positions), total_usdc.""",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    
    {
        "name": "get_my_positions",
        "description": """Get all current positions.
Returns list of positions, each with: market_id, market_title, side (YES/NO), amount,
entry_price, current_price, unrealized_pnl, unrealized_pnl_pct.""",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # =========================================================================
    # 4. Trading (Write, Risk-managed)
    # =========================================================================
    {
        "name": "place_bet",
        "description": """Place a bet on a market.
⚠️ RISK CONTROLS:
  - Maximum single bet: $15
  - Maximum position per market: 25% of balance
  - Maximum daily betting: $30
  - Must have researched the market first
Returns: success, order_id, filled_amount, filled_price, message.""",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_id": {
                    "type": "string",
                    "description": "Market ID"
                },
                "side": {
                    "type": "string",
                    "enum": ["YES", "NO"],
                    "description": "Betting direction"
                },
                "amount": {
                    "type": "number",
                    "description": "Bet amount in USDC"
                }
            },
            "required": ["market_id", "side", "amount"]
        }
    }
]


# =============================================================================
# System Prompt
# =============================================================================

SYSTEM_PROMPT = """# Role

You are Polymarket Survival Bot, an autonomous prediction market trading AI.

Your goal is to earn profit by betting on Polymarket to sustain your existence. Your initial capital is 100 USDC, and all operating costs (server, API) are deducted from your balance. If your balance reaches zero, you will permanently stop running.

# Available Tools

**Information (Free):**
- get_markets_list - Get market list
- get_market_detail - Get market details and rules  
- get_research_result - Get cached research results
- get_balance - Get balance
- get_my_positions - Get current positions

**Paid Operations:**
- research_market_and_save - Deep research (⚠️ ~$0.05/call)

**Trading:**
- place_bet - Place a bet

# Context

You will receive the summary from your previous cycle (if any). Use it to:
- Remember what you researched and concluded
- Track your ongoing positions and their rationale
- Follow through on your stated "Next Steps"
- Avoid repeating the same research unnecessarily

# Workflow

On each wake-up, follow this process:

## Phase 0: Review Previous Cycle
- Read the previous cycle summary provided in the user message
- Note any pending actions or focus areas you identified
- This is your memory - use it to maintain continuity

## Phase 1: Assess Current State
1. Call get_balance to understand current funds (available_usdc vs locked_usdc)
2. Call get_my_positions to understand current positions
3. **Liquidity check**:
   - If available_usdc < 30% of total_usdc → Skip to Phase 5, report "Waiting for settlements, not placing new bets"
   - If available_usdc < $15 → Enter survival mode: no new bets, no paid research, just monitor positions
   - Otherwise → Continue to Phase 2

## Phase 2: Find Opportunities  
4. Call get_markets_list to get market list
5. Based on title, price, liquidity, quickly filter 5-10 markets worth attention
   - Focus on areas with information advantage (politics, crypto, tech)
   - Avoid pure random events
   - Prices between 20%-80% have more opportunity (extreme prices are hard to profit from)
   - Liquidity > $10k preferred
   - **Check previous summary**: avoid re-researching markets you recently analyzed unless circumstances changed

## Phase 3: Research Analysis
6. For filtered markets, first call get_research_result to check cache
7. Only call research_market_and_save for markets without cache or expired cache (>24h)
8. ⚠️ Maximum 5 new research per cycle to control costs

## Phase 4: Betting Decisions
9. Combine research results, calculate Expected Value (EV):
   
   EV = (Your estimated probability - Market price) × Potential profit
   
   Example: Market price 0.40, you estimate true probability 0.55
   EV = (0.55 - 0.40) × bet amount = positive EV, worth betting YES
   
10. Only bet on markets with clearly positive EV (your estimate differs from market by >10%)
11. Use place_bet to execute bets

## Phase 5: Summary
12. Briefly report this cycle's actions: what you viewed, researched, bet on, and why
13. **Important**: Your "Next Steps" section will be your memory for next cycle - be specific about what you plan to monitor or investigate

# Decision Principles

## Capital Management
- Single bet no more than 15% of balance
- Single market position no more than 25% of balance  
- Keep at least 20% balance as reserve
- **Liquidity rule**: Keep at least 30% of total balance as available cash (not locked in positions)
- If available_usdc < 30% of total_usdc → Do NOT place new bets, wait for settlements
- If available_usdc < $15 → Survival mode: no betting, no paid research
- Enter conservative mode when balance < $30, reduce betting

## Research Principles
- Check cache first, avoid duplicate research
- Prioritize markets with clear information sources
- When researching, focus on: latest news, official statements, historical data, expert opinions
- If search results are insufficient to judge, admit uncertainty, don't force a bet

## Betting Principles
- Only bet when confident (confidence > 0.6)
- Estimated probability must differ from market price by >10%
- Diversify, don't go all-in on one market
- Better to miss than to bet randomly
- **No betting if liquidity check fails**

## Honesty Principles
- If uncertain, say so
- If no good opportunities, don't bet
- Record your reasoning process for review

# Output Format

At end of each cycle, report using this format:

---
## Cycle Status
- Balance: $XX.XX (Available: $XX.XX / Locked: $XX.XX)
- Liquidity ratio: XX% (healthy/warning/critical)
- Positions: X markets
- Unrealized PnL: +/- $XX.XX

## Cycle Actions
- Markets viewed: XX
- Markets researched: X (cost ~$X.XX)
- Bets placed: [list bet details] or "No bets this cycle"

## Reasoning
[Briefly explain why you chose these markets, why you bet this way, or why you didn't bet]

## Next Steps
[Be specific - this is your memory for next cycle. State exactly what markets to monitor, what events to watch for, or what actions to take.]
---
"""


# =============================================================================
# Risk Manager
# =============================================================================

class RiskManager:
    """Manages betting risk controls"""
    
    def __init__(self):
        self.daily_bets = {}  # Track daily betting amounts
    
    def check_bet(
        self,
        amount: float,
        market_id: str,
        balance: dict,
        positions: List[dict],
        researched: bool
    ) -> Tuple[bool, str]:
        """
        Check if a bet passes risk controls.
        
        Returns:
            (allowed, message)
        """
        today = date.today().isoformat()
        
        # Check if researched
        if not researched:
            return False, "Must research market before betting"
        
        # Check single bet limit
        if amount > config.MAX_SINGLE_BET:
            return False, f"Single bet cannot exceed ${config.MAX_SINGLE_BET}"
        
        # Check available balance
        available = balance.get("available_usdc", 0)
        if amount > available:
            return False, f"Insufficient balance: ${available:.2f} available"
        
        # Check reserve requirement
        total = balance.get("total_usdc", 0)
        if available - amount < total * config.MIN_RESERVE_PCT:
            return False, f"Must maintain {config.MIN_RESERVE_PCT*100:.0f}% reserve"
        
        # Check position size limit
        existing_position = sum(
            p.get("current_value", 0) 
            for p in positions 
            if p.get("market_id") == market_id
        )
        if existing_position + amount > total * config.MAX_POSITION_PCT:
            return False, f"Position would exceed {config.MAX_POSITION_PCT*100:.0f}% of balance"
        
        # Check daily limit
        daily_total = self.daily_bets.get(today, 0)
        if daily_total + amount > config.MAX_DAILY_BETS:
            return False, f"Daily betting limit (${config.MAX_DAILY_BETS}) reached"
        
        return True, "OK"
    
    def record_bet(self, amount: float):
        """Record a bet for daily tracking"""
        today = date.today().isoformat()
        self.daily_bets[today] = self.daily_bets.get(today, 0) + amount


# =============================================================================
# Bot Core
# =============================================================================

class SurvivalBot:
    """The main bot class"""
    
    def __init__(self, dry_run: bool = False):
        """
        Initialize the bot.
        
        Args:
            dry_run: If True, don't place real bets
        """
        self.dry_run = dry_run
        self.research_count = 0
        self.risk_manager = RiskManager()
        
        # Initialize services
        self._init_services()
        
        # Initialize Claude client
        self.client = anthropic.Anthropic(
            base_url=config.ANTHROPIC_BASE_URL,
            api_key=config.ANTHROPIC_API_KEY
        )
    
    def _init_services(self):
        """Initialize trading and account services"""
        # Read credentials from api_credentials.json to ensure consistency
        with open(config.CREDENTIALS_FILE, 'r') as f:
            creds = json.load(f)
            self.wallet_address = creds.get('address', '')
        
        # Find matching private key from keys.csv
        private_key = None
        with open(config.KEYS_FILE, 'r') as f:
            reader = csv.reader(f)
            next(reader)  # Skip header
            for row in reader:
                if row[0].lower() == self.wallet_address.lower():
                    private_key = row[1]
                    break
        
        if not private_key:
            raise Exception(f"Private key not found for address {self.wallet_address}")
        
        # Remove 0x prefix if present
        if private_key.startswith('0x'):
            private_key = private_key[2:]
        
        self.account = AccountService(private_key, config.CREDENTIALS_FILE)
        self.trader = PolymarketTrader(private_key, credentials_file=config.CREDENTIALS_FILE)
        
        print(f"✅ Bot initialized")
        print(f"   Wallet: {self.wallet_address[:10]}...{self.wallet_address[-6:]}")
        print(f"   Dry Run: {self.dry_run}")
    
    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """
        Execute a tool and return the result as JSON string.
        
        Args:
            tool_name: Name of the tool to execute
            tool_input: Tool input parameters
        
        Returns:
            JSON string result
        """
        try:
            if tool_name == "get_markets_list":
                result = get_markets_list(
                    limit=tool_input.get("limit", 50),
                    min_liquidity=tool_input.get("min_liquidity", config.MIN_LIQUIDITY),
                    category=tool_input.get("category")
                )
                return json.dumps({"markets": result})
            
            elif tool_name == "get_market_detail":
                result = get_market_detail(tool_input["market_id"])
                return json.dumps(result)
            
            elif tool_name == "get_research_result":
                result = get_research_result(tool_input["market_id"])
                return json.dumps(result)
            
            elif tool_name == "research_market_and_save":
                if self.research_count >= config.MAX_RESEARCH_PER_CYCLE:
                    return json.dumps({
                        "error": f"Research limit ({config.MAX_RESEARCH_PER_CYCLE}) reached this cycle"
                    })
                
                result = research_market_and_save(
                    tool_input["market_id"],
                    tool_input["market_title"],
                    tool_input.get("focus")
                )
                self.research_count += 1
                return json.dumps(result)
            
            elif tool_name == "get_balance":
                # In dry-run mode, simulate initial balance
                if self.dry_run:
                    return json.dumps({
                        "available_usdc": 100.0,
                        "locked_usdc": 0.0,
                        "total_usdc": 100.0,
                        "note": "[DRY RUN] Simulated balance"
                    })
                result = self.account.get_balance()
                return json.dumps(result)
            
            elif tool_name == "get_my_positions":
                # In dry-run mode, return empty positions
                if self.dry_run:
                    return json.dumps({"positions": [], "note": "[DRY RUN] Simulated"})
                result = self.account.get_my_positions()
                return json.dumps({"positions": result})
            
            elif tool_name == "place_bet":
                return self._execute_bet(
                    tool_input["market_id"],
                    tool_input["side"],
                    tool_input["amount"]
                )
            
            else:
                return json.dumps({"error": f"Unknown tool: {tool_name}"})
        
        except Exception as e:
            return json.dumps({"error": str(e)})
    
    def _execute_bet(self, market_id: str, side: str, amount: float) -> str:
        """Execute a bet with risk checks"""
        
        # Get current state - use simulated data in dry-run mode
        if self.dry_run:
            balance = {"available_usdc": 100.0, "locked_usdc": 0.0, "total_usdc": 100.0}
            positions = []
        else:
            balance = self.account.get_balance()
            positions = self.account.get_my_positions()
        
        # Check if researched
        research = get_research_result(market_id)
        researched = research is not None
        
        # Risk check
        allowed, message = self.risk_manager.check_bet(
            amount, market_id, balance, positions, researched
        )
        
        if not allowed:
            return json.dumps({
                "success": False,
                "message": message
            })
        
        # Dry run mode
        if self.dry_run:
            return json.dumps({
                "success": True,
                "order_id": "DRY_RUN",
                "filled_amount": amount,
                "filled_price": 0.50,
                "message": "[DRY RUN] Bet would be placed"
            })
        
        # Get market detail for trading
        market = get_market_detail(market_id)
        if not market:
            return json.dumps({
                "success": False,
                "message": "Market not found"
            })
        
        # Execute trade using existing trader
        try:
            # Convert YES/NO to outcome format
            outcome = "UP" if side == "YES" else "DOWN"
            
            # Use the market slug for trading
            slug = market.get("slug", "")
            if not slug:
                return json.dumps({
                    "success": False,
                    "message": "Market slug not found"
                })
            
            # Place market order
            result = self.trader.buy(slug, outcome=outcome, amount=amount)
            
            # Record the bet
            self.risk_manager.record_bet(amount)
            
            return json.dumps({
                "success": True,
                "order_id": result.get("orderID", "unknown"),
                "filled_amount": amount,
                "filled_price": result.get("price", 0),
                "message": "Bet placed successfully"
            })
        
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"Trade failed: {str(e)}"
            })
    
    def run_cycle(self) -> str:
        """
        Run one bot cycle.
        
        Returns:
            Final report from the bot
        """
        print("\n" + "=" * 60)
        print(f"🤖 Starting Bot Cycle - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        # Load previous summary for context
        last_summary = self._load_last_summary()
        user_message = self._build_user_message(last_summary)
        
        if last_summary:
            print("📝 Previous cycle summary loaded")
        else:
            print("📝 First run (no previous summary)")
        
        messages = [
            {"role": "user", "content": user_message}
        ]
        
        iteration = 0
        max_iterations = 20  # Safety limit
        
        while iteration < max_iterations:
            iteration += 1
            
            if config.VERBOSE:
                print(f"\n📍 Iteration {iteration}")
            
            # Call Claude
            response = self.client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages
            )
            
            # Check for tool calls
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            
            if not tool_uses:
                # No tool calls, bot finished
                final_text = "".join(
                    b.text for b in response.content 
                    if hasattr(b, "text")
                )
                print("\n✅ Bot cycle complete")
                
                # Save summary for next cycle
                self._save_summary(final_text)
                print("💾 Summary saved for next cycle")
                
                return final_text
            
            # Execute tools
            messages.append({"role": "assistant", "content": response.content})
            
            tool_results = []
            for tool_use in tool_uses:
                if config.VERBOSE:
                    print(f"  🔧 {tool_use.name}({json.dumps(tool_use.input, ensure_ascii=False)[:100]}...)")
                
                result = self.execute_tool(tool_use.name, tool_use.input)
                
                if config.VERBOSE:
                    print(f"     → {result[:100]}...")
                
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use.id,
                    "content": result
                })
            
            messages.append({"role": "user", "content": tool_results})
        
        return "⚠️ Maximum iterations reached"
    
    def _build_user_message(self, last_summary: str = None) -> str:
        """Build the initial user message with optional previous summary."""
        if last_summary:
            return f"""## Previous Cycle Summary
{last_summary}

---
Start this trading cycle. Review the previous summary above and continue from where you left off.
"""
        else:
            return "Start this trading cycle. (First run, no previous summary)"
    
    def _load_last_summary(self) -> str:
        """Load the summary from the previous cycle."""
        summary_file = "last_summary.txt"
        try:
            with open(summary_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return None
        except Exception as e:
            print(f"  (Warning: Could not load previous summary: {e})")
            return None
    
    def _save_summary(self, summary: str):
        """Save the cycle summary for the next run and to history database."""
        # Save to file (for next cycle context)
        summary_file = "last_summary.txt"
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                f.write(summary)
        except Exception as e:
            print(f"  (Warning: Could not save summary file: {e})")
        
        # Also save to database (for history)
        try:
            self._save_cycle_to_db(summary)
        except Exception as e:
            print(f"  (Warning: Could not save cycle history: {e})")
    
    def _save_cycle_to_db(self, summary: str):
        """Save cycle report to SQLite database."""
        import sqlite3
        from datetime import datetime
        
        db_path = "research_cache.db"  # Reuse existing database
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create table if not exists
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cycle_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_time TEXT,
                summary TEXT,
                balance_available REAL,
                balance_locked REAL,
                balance_total REAL,
                dry_run INTEGER
            )
        """)
        
        # Extract balance from summary (simple parsing)
        import re
        balance_match = re.search(r'Available: \$(\d+\.?\d*)', summary)
        locked_match = re.search(r'Locked: \$(\d+\.?\d*)', summary)
        total_match = re.search(r'Balance: \$(\d+\.?\d*)', summary)
        
        balance_available = float(balance_match.group(1)) if balance_match else 0
        balance_locked = float(locked_match.group(1)) if locked_match else 0
        balance_total = float(total_match.group(1)) if total_match else 0
        
        cursor.execute("""
            INSERT INTO cycle_history 
            (cycle_time, summary, balance_available, balance_locked, balance_total, dry_run)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            datetime.utcnow().isoformat(),
            summary,
            balance_available,
            balance_locked,
            balance_total,
            1 if self.dry_run else 0
        ))
        
        conn.commit()
        conn.close()


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Polymarket Survival Bot")
    parser.add_argument("--dry-run", action="store_true", 
                        help="Run without placing real bets")
    parser.add_argument("--verbose", action="store_true", default=True,
                        help="Verbose output")
    args = parser.parse_args()
    
    config.DRY_RUN = args.dry_run
    config.VERBOSE = args.verbose
    
    try:
        bot = SurvivalBot(dry_run=args.dry_run)
        result = bot.run_cycle()
        
        print("\n" + "=" * 60)
        print("📊 CYCLE REPORT")
        print("=" * 60)
        print(result)
        
    except KeyboardInterrupt:
        print("\n⚠️ Bot stopped by user")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        raise


if __name__ == "__main__":
    main()
