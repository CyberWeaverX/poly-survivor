"""
Polymarket Survival Bot Configuration
"""

import os
import json

# Load secrets from file
SECRETS_CONFIG_FILE = "secrets/config.json"
try:
    with open(SECRETS_CONFIG_FILE, 'r') as f:
        _secrets = json.load(f)
except FileNotFoundError:
    _secrets = {}

# API Configuration
ANTHROPIC_BASE_URL = _secrets.get("ANTHROPIC_BASE_URL", "https://api.gptsapi.net")
ANTHROPIC_API_KEY = _secrets.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

# Polymarket API
GAMMA_API_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon

# Credentials (in secrets/ folder - DO NOT commit to git!)
CREDENTIALS_FILE = "secrets/api_credentials.json"
KEYS_FILE = "secrets/keys.csv"

# Research
RESEARCH_DB_PATH = "research_cache.db"
RESEARCH_CACHE_HOURS = 24
MAX_RESEARCH_PER_CYCLE = 5
RESEARCH_COST_USD = 0.05  # Approximate cost per research

# Risk Management
MAX_SINGLE_BET = 15.0           # Maximum single bet amount
MAX_POSITION_PCT = 0.25         # Maximum position size as % of balance
MAX_DAILY_BETS = 30.0           # Maximum daily betting amount
MIN_RESERVE_PCT = 0.20          # Minimum reserve as % of balance
MIN_CONFIDENCE = 0.6            # Minimum confidence to bet
MIN_EDGE = 0.10                 # Minimum edge (probability difference)

# Market Filters
MIN_LIQUIDITY = 5000            # Minimum market liquidity
EXCLUDED_CATEGORIES = {"sports", "nfl", "nba", "mlb", "soccer", "esports", "price"}

# Bot Behavior
DRY_RUN = False                 # If True, don't actually place bets
VERBOSE = True                  # Detailed logging
