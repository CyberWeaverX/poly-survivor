"""
Polymarket Trading Module
Supports limit orders, market orders, and order management
"""

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
import json
import time
import requests


class PolymarketTrader:
    """Polymarket Trading Client"""
    
    def __init__(self, private_key, chain_id=137, credentials_file='secrets/api_credentials.json'):
        """
        Initialize trading client
        
        Args:
            private_key: Wallet private key (without 0x prefix)
            chain_id: Chain ID (137 = Polygon)
            credentials_file: API credentials file path
        """
        self.host = "https://clob.polymarket.com"
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.chain_id = chain_id
        
        # Create client
        self.client = ClobClient(
            host=self.host,
            key=private_key,
            chain_id=chain_id
        )
        
        # Load API credentials
        self._load_credentials(credentials_file)
        
        print(f"✅ Trading client initialized")
        print(f"   Wallet: {self.wallet_address}")
    
    def _load_credentials(self, credentials_file):
        """Load API credentials"""
        try:
            with open(credentials_file, 'r') as f:
                creds_data = json.load(f)
            
            self.wallet_address = creds_data['address']
            
            self.client.set_api_creds(ApiCreds(
                api_key=creds_data['api_key'],
                api_secret=creds_data['api_secret'],
                api_passphrase=creds_data['api_passphrase']
            ))
            
        except FileNotFoundError:
            raise Exception(f"Credentials file not found: {credentials_file}")
        except Exception as e:
            raise Exception(f"Failed to load credentials: {e}")
    
    def get_event_info(self, event_slug):
        """
        Get event information
        
        Args:
            event_slug: Event slug (e.g., 'btc-updown-15m-1766589300')
        
        Returns:
            dict: Event information including token IDs
        """
        url = f"{self.gamma_url}/events/slug/{event_slug}"
        
        try:
            response = requests.get(url, timeout=10)
            
            if response.status_code == 200:
                event = response.json()
                
                # Extract token IDs
                markets = event.get('markets', [])
                if markets:
                    market = markets[0]
                    token_ids = json.loads(market.get('clobTokenIds', '[]'))
                    
                    if len(token_ids) >= 2:
                        return {
                            'title': event.get('title', 'N/A'),
                            'start_time': event.get('startDate'),
                            'end_time': event.get('endDate'),
                            'up_token_id': token_ids[0],
                            'down_token_id': token_ids[1],
                            'market_id': market.get('id')
                        }
            
            return None
            
        except Exception as e:
            print(f"Failed to get event info: {e}")
            return None
    
    def get_current_event(self, asset='BTC'):
        """
        Get current active event
        
        Args:
            asset: Asset name (BTC, ETH, SOL, XRP)
        
        Returns:
            str: Event slug
        """
        now = datetime.now(timezone.utc)
        minutes = (now.minute // 15) * 15
        aligned_time = now.replace(minute=minutes, second=0, microsecond=0)
        timestamp = int(aligned_time.timestamp())
        
        return f"{asset.lower()}-updown-15m-{timestamp}"
    
    def get_orderbook(self, token_id_or_event, side=None):
        """
        Get order book
        
        Args:
            token_id_or_event: Token ID or event_slug
            side: 'UP' or 'DOWN' (if providing event_slug)
        
        Returns:
            OrderBookSummary: Order book data
        """
        # If it's an event_slug
        if isinstance(token_id_or_event, str) and '-' in token_id_or_event and side:
            event_info = self.get_event_info(token_id_or_event)
            if not event_info:
                return None
            token_id = event_info['up_token_id'] if side == 'UP' else event_info['down_token_id']
        else:
            token_id = token_id_or_event
        
        return self.client.get_order_book(token_id)
    
    def get_market_price(self, event_slug, side):
        """
        Get market price (mid price)
        
        Args:
            event_slug: Event slug
            side: 'UP' or 'DOWN'
        
        Returns:
            float: Market price (0-1)
        """
        orderbook = self.get_orderbook(event_slug, side)
        if not orderbook:
            return None
        
        # Handle OrderBookSummary object
        bids = orderbook.bids if hasattr(orderbook, 'bids') else []
        asks = orderbook.asks if hasattr(orderbook, 'asks') else []
        
        if not bids or not asks:
            return None
        
        best_bid = float(bids[0].price) if bids else 0
        best_ask = float(asks[-1].price) if asks else 1
        
        # Return mid price
        return (best_bid + best_ask) / 2
    
    def create_limit_order(self, token_id, side, price, amount, outcome='UP'):
        """
        Create limit order
        
        Args:
            token_id: Token ID
            side: BUY or SELL
            price: Price (0-1)
            amount: Amount (USDC)
            outcome: 'UP' or 'DOWN'
        
        Returns:
            SignedOrder: Signed order
        """
        # Calculate shares
        shares = amount / price
        
        # Check minimum shares (Polymarket requires at least 5 shares)
        MIN_SHARES = 5.0
        if shares < MIN_SHARES:
            # Auto-adjust amount
            amount = MIN_SHARES * price
            shares = MIN_SHARES
            print(f"⚠️  Auto-adjusted amount: ${amount:.2f} (minimum {MIN_SHARES} shares)")
        
        # Precision handling
        price_decimal = Decimal(str(price)).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
        shares_decimal = Decimal(str(shares)).quantize(Decimal('0.0001'), rounding=ROUND_DOWN)
        
        # Create order
        order_args = OrderArgs(
            token_id=token_id,
            price=float(price_decimal),
            size=float(shares_decimal),
            side=side,
            fee_rate_bps=0,
        )
        
        return self.client.create_order(order_args)
    
    def create_market_order(self, token_id, side, amount, outcome='UP'):
        """
        Create market order (limit order at best current price)
        
        Args:
            token_id: Token ID
            side: BUY or SELL
            amount: Amount (USDC)
            outcome: 'UP' or 'DOWN'
        
        Returns:
            SignedOrder: Signed order
        """
        # Get order book
        book = self.get_orderbook(token_id)
        
        # Determine price
        if side == BUY:
            # Buy at lowest ask price
            if hasattr(book, 'asks') and book.asks and len(book.asks) > 0:
                price = float(book.asks[-1].price)
            else:
                raise Exception("Cannot get ask price")
        else:
            # Sell at highest bid price
            if hasattr(book, 'bids') and book.bids and len(book.bids) > 0:
                price = float(book.bids[-1].price)
            else:
                raise Exception("Cannot get bid price")
        
        print(f"Market order using price: ${price:.2f}")
        
        # Create limit order
        return self.create_limit_order(token_id, side, price, amount, outcome)
    
    def place_order(self, signed_order, order_type=OrderType.GTC):
        """
        Submit order
        
        Args:
            signed_order: Signed order
            order_type: Order type (GTC, FOK, IOC)
        
        Returns:
            dict: Order response
        """
        try:
            resp = self.client.post_order(signed_order, order_type)
            return resp
            
        except Exception as e:
            raise
    
    def cancel_order(self, order_id):
        """
        Cancel order
        
        Args:
            order_id: Order ID
        
        Returns:
            bool: Success status
        """
        try:
            self.client.cancel(order_id)
            return True
        except Exception as e:
            return False
    
    def get_order_status(self, order_id):
        """
        Get order status
        
        Args:
            order_id: Order ID
        
        Returns:
            dict: Order status
        """
        try:
            return self.client.get_order(order_id)
        except Exception as e:
            print(f"❌ Failed to get order: {e}")
            return None
    
    def get_orders(self):
        """
        Get all orders
        
        Returns:
            list: Order list
        """
        try:
            return self.client.get_orders()
        except Exception as e:
            print(f"❌ Failed to get orders: {e}")
            return []
    
    def buy(self, event_slug, outcome='UP', amount=2.0, price=None):
        """
        Buy (convenience method)
        
        Args:
            event_slug: Event slug
            outcome: 'UP' or 'DOWN'
            amount: Amount (USDC)
            price: Price (None = market order)
        
        Returns:
            dict: Order response
        """
        print(f"\nBuying {outcome}")
        
        # Get event info
        event_info = self.get_event_info(event_slug)
        if not event_info:
            raise Exception(f"Event not found: {event_slug}")
        
        # Select token
        token_id = event_info['up_token_id'] if outcome == 'UP' else event_info['down_token_id']
        
        # Create order
        if price is None:
            # Market order
            signed_order = self.create_market_order(token_id, BUY, amount, outcome)
        else:
            # Limit order
            signed_order = self.create_limit_order(token_id, BUY, price, amount, outcome)
        
        # Submit order
        return self.place_order(signed_order)
    
    def sell(self, event_slug, outcome='UP', amount=2.0, price=None):
        """
        Sell (convenience method)
        
        Args:
            event_slug: Event slug
            outcome: 'UP' or 'DOWN'
            amount: Amount (USDC)
            price: Price (None = market order)
        
        Returns:
            dict: Order response
        """
        print(f"\n{'='*60}")
        print(f"Selling {outcome}")
        print(f"{'='*60}")
        
        # Get event info
        event_info = self.get_event_info(event_slug)
        if not event_info:
            raise Exception(f"Event not found: {event_slug}")
        
        print(f"Event: {event_info['title']}")
        
        # Select token
        token_id = event_info['up_token_id'] if outcome == 'UP' else event_info['down_token_id']
        
        # Create order
        if price is None:
            # Market order
            signed_order = self.create_market_order(token_id, SELL, amount, outcome)
        else:
            # Limit order
            signed_order = self.create_limit_order(token_id, SELL, price, amount, outcome)
        
        # Submit order
        return self.place_order(signed_order)
