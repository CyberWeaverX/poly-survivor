"""
Polymarket Account Service
Provides balance and position queries
"""

import requests
import json
from typing import List, Optional
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType


CLOB_HOST = "https://clob.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CHAIN_ID = 137  # Polygon


class AccountService:
    """Account service for balance and position queries"""
    
    def __init__(self, private_key: str, credentials_file: str = "secrets/api_credentials.json"):
        """
        Initialize account service.
        
        Args:
            private_key: Wallet private key (without 0x prefix)
            credentials_file: Path to API credentials JSON file
        """
        self.private_key = private_key
        self.credentials_file = credentials_file
        self.wallet_address = None
        
        # Initialize CLOB client
        self.client = ClobClient(
            host=CLOB_HOST,
            key=private_key,
            chain_id=CHAIN_ID
        )
        
        # Load credentials
        self._load_credentials()
    
    def _load_credentials(self):
        """Load API credentials from file"""
        try:
            with open(self.credentials_file, 'r') as f:
                creds = json.load(f)
            
            self.wallet_address = creds.get("address", "")
            
            self.client.set_api_creds(ApiCreds(
                api_key=creds["api_key"],
                api_secret=creds["api_secret"],
                api_passphrase=creds["api_passphrase"]
            ))
            
            # Initialize signature type (required for balance queries)
            try:
                self.client.create_or_derive_api_creds()
            except:
                pass  # May already be set
                
        except FileNotFoundError:
            raise Exception(f"Credentials file not found: {self.credentials_file}")
        except Exception as e:
            raise Exception(f"Failed to load credentials: {e}")
    
    def get_balance(self) -> dict:
        """
        Get account balance.
        
        Returns:
            Dict with: available_usdc, locked_usdc, total_usdc
        """
        try:
            # Get positions to calculate locked value first
            positions = self.get_my_positions()
            locked_value = sum(
                abs(float(p.get("current_value", 0)))
                for p in positions
            )
            
            # Try to get balance from CLOB API
            try:
                # Need to pass params with signature_type for CLOB API
                params = BalanceAllowanceParams(
                    asset_type=AssetType.COLLATERAL,
                    signature_type=0  # EOA signature
                )
                balance_info = self.client.get_balance_allowance(params)
                
                # Parse balance - it's typically in wei format (6 decimals for USDC)
                if hasattr(balance_info, 'balance'):
                    raw_balance = float(balance_info.balance) / 1e6
                elif isinstance(balance_info, dict):
                    raw_balance = float(balance_info.get('balance', 0)) / 1e6
                else:
                    raw_balance = locked_value  # Use position value as estimate
            except Exception as e:
                print(f"  (Balance API unavailable: {e})")
                raw_balance = locked_value  # Use position value as estimate
            
            return {
                "available_usdc": max(0, raw_balance - locked_value),
                "locked_usdc": locked_value,
                "total_usdc": max(raw_balance, locked_value)
            }
        except Exception as e:
            print(f"Error getting balance: {e}")
            return {
                "available_usdc": 0,
                "locked_usdc": 0,
                "total_usdc": 0,
                "error": str(e)
            }
    
    def get_my_positions(self) -> List[dict]:
        """
        Get current positions.
        
        Returns:
            List of position dicts with: market_id, market_title, side, amount,
            entry_price, current_price, unrealized_pnl, unrealized_pnl_pct
        """
        if not self.wallet_address:
            return []
        
        try:
            response = requests.get(
                f"{DATA_API_URL}/positions",
                params={
                    "user": self.wallet_address.lower(),
                    "limit": 100
                },
                timeout=15
            )
            response.raise_for_status()
            raw_positions = response.json()
        except Exception as e:
            print(f"Error fetching positions: {e}")
            return []
        
        positions = []
        
        for pos in raw_positions:
            # Skip positions with no value
            current_value = float(pos.get("currentValue", 0) or 0)
            if current_value <= 0:
                continue
            
            # Calculate PnL
            initial_value = float(pos.get("initialValue", 0) or 0)
            unrealized_pnl = current_value - initial_value
            unrealized_pnl_pct = (unrealized_pnl / initial_value * 100) if initial_value > 0 else 0
            
            # Get current price
            current_price = float(pos.get("curPrice", 0) or 0)
            
            # Get entry price (average price)
            size = float(pos.get("size", 0) or 0)
            entry_price = initial_value / size if size > 0 else 0
            
            positions.append({
                "market_id": pos.get("eventId"),
                "market_title": pos.get("title", "Unknown"),
                "side": pos.get("outcome", "YES"),
                "amount": size,
                "entry_price": entry_price,
                "current_price": current_price,
                "current_value": current_value,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pnl_pct
            })
        
        return positions


def get_balance_simple(wallet_address: str) -> dict:
    """
    Simple balance query using Data API only.
    Does not require API credentials.
    
    Args:
        wallet_address: Wallet address
    
    Returns:
        Dict with holdings value
    """
    try:
        response = requests.get(
            f"{DATA_API_URL}/holdings",
            params={"user": wallet_address.lower()},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        
        total_value = sum(
            float(h.get("value", 0) or 0) 
            for h in data
        )
        
        return {
            "total_holdings_value": total_value,
            "holdings_count": len(data)
        }
    except Exception as e:
        print(f"Error fetching holdings: {e}")
        return {"total_holdings_value": 0, "holdings_count": 0}


if __name__ == "__main__":
    import csv
    
    print("=" * 60)
    print("Testing Account Module")
    print("=" * 60)
    
    # Read private key from keys.csv
    try:
        with open("secrets/keys.csv", "r") as f:
            reader = csv.reader(f)
            next(reader)  # Skip header
            row = next(reader)
            wallet_address = row[0]
            private_key = row[1]
        
        print(f"\nWallet: {wallet_address[:10]}...{wallet_address[-6:]}")
        
        # Test simple holdings query
        print("\n[1] Testing simple holdings query...")
        holdings = get_balance_simple(wallet_address)
        print(f"  Total Holdings Value: ${holdings['total_holdings_value']:.2f}")
        print(f"  Holdings Count: {holdings['holdings_count']}")
        
        # Test full account service
        print("\n[2] Initializing AccountService...")
        account = AccountService(private_key)
        
        print("\n[3] Getting balance...")
        balance = account.get_balance()
        print(f"  Available: ${balance['available_usdc']:.2f}")
        print(f"  Locked: ${balance['locked_usdc']:.2f}")
        print(f"  Total: ${balance['total_usdc']:.2f}")
        
        print("\n[4] Getting positions...")
        positions = account.get_my_positions()
        print(f"  Found {len(positions)} positions")
        
        for i, pos in enumerate(positions[:5], 1):
            print(f"\n  {i}. {pos['market_title'][:40]}...")
            print(f"     Side: {pos['side']}")
            print(f"     Value: ${pos['current_value']:.2f}")
            print(f"     PnL: ${pos['unrealized_pnl']:+.2f} ({pos['unrealized_pnl_pct']:+.1f}%)")
        
    except FileNotFoundError:
        print("Error: keys.csv not found")
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n" + "=" * 60)
    print("Test complete!")
