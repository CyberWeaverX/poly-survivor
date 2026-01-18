"""
Polymarket Research Service
Uses Claude Web Search for market research with SQLite caching
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import Optional
import anthropic

import config


# Configuration (loaded from config.py which reads secrets/config.json)
ANTHROPIC_BASE_URL = config.ANTHROPIC_BASE_URL
ANTHROPIC_API_KEY = config.ANTHROPIC_API_KEY
DATABASE_PATH = "research_cache.db"
CACHE_EXPIRY_HOURS = 24


class ResearchService:
    """Research service with caching and web search"""
    
    def __init__(self, db_path: str = DATABASE_PATH):
        """
        Initialize research service.
        
        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path
        self.client = anthropic.Anthropic(
            base_url=ANTHROPIC_BASE_URL,
            api_key=ANTHROPIC_API_KEY
        )
        self._init_db()
    
    def _init_db(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS research (
                market_id TEXT PRIMARY KEY,
                market_title TEXT,
                research_time TEXT,
                summary TEXT,
                estimated_probability REAL,
                confidence REAL,
                key_factors TEXT,
                sources TEXT
            )
        """)
        
        conn.commit()
        conn.close()
    
    def get_research_result(self, market_id: str) -> Optional[dict]:
        """
        Get cached research result.
        
        Args:
            market_id: Market ID
        
        Returns:
            Research result dict or None if not found/expired
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM research WHERE market_id = ?",
            (market_id,)
        )
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        # Check expiry
        research_time = datetime.fromisoformat(row[2])
        if datetime.utcnow() - research_time > timedelta(hours=CACHE_EXPIRY_HOURS):
            return None  # Expired
        
        return {
            "market_id": row[0],
            "market_title": row[1],
            "research_time": row[2],
            "summary": row[3],
            "estimated_probability": row[4],
            "confidence": row[5],
            "key_factors": json.loads(row[6]) if row[6] else [],
            "sources": json.loads(row[7]) if row[7] else []
        }
    
    def research_market_and_save(
        self,
        market_id: str,
        market_title: str,
        focus: str = None,
        market_description: str = None
    ) -> dict:
        """
        Research a market using Claude Web Search and save result.
        
        Args:
            market_id: Market ID
            market_title: Market title for search
            focus: Optional research focus (e.g., 'focus on recent news')
            market_description: Optional market rules/description
        
        Returns:
            Research result dict with: summary, estimated_probability, 
            confidence, key_factors, sources
        """
        # Build research prompt
        prompt = f"""Analyze this prediction market and provide your assessment:

**Market Question:** {market_title}

{f'**Market Rules:** {market_description[:1000]}' if market_description else ''}

{f'**Research Focus:** {focus}' if focus else ''}

Please search for the latest relevant information and provide:
1. A brief summary of the current situation (3-5 sentences)
2. Your estimated probability that this resolves "Yes" (0.0 to 1.0)
3. Your confidence level in this estimate (0.0 to 1.0)
4. Key factors affecting this outcome (list of 3-5 factors)

Respond in this exact JSON format:
{{
    "summary": "Your analysis summary...",
    "estimated_probability": 0.XX,
    "confidence": 0.XX,
    "key_factors": ["Factor 1", "Factor 2", "Factor 3"]
}}

Be honest about uncertainty. If you cannot find enough information, set confidence below 0.5.
"""
        
        try:
            response = self.client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=2048,
                tools=[{
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3
                }],
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Extract sources from web search results
            sources = []
            for block in response.content:
                if hasattr(block, 'type') and block.type == "web_search_tool_result":
                    if hasattr(block, 'content') and isinstance(block.content, list):
                        for result in block.content:
                            if hasattr(result, 'url') and hasattr(result, 'title'):
                                sources.append({
                                    "title": result.title,
                                    "url": result.url
                                })
            
            # Extract the text response
            text_response = ""
            for block in response.content:
                if hasattr(block, 'type') and block.type == "text":
                    text_response = block.text
                    break
            
            # Parse JSON from response
            result = self._parse_research_response(text_response)
            result["sources"] = sources[:5]  # Limit to 5 sources
            
        except Exception as e:
            print(f"Research error: {e}")
            result = {
                "summary": f"Research failed: {str(e)}",
                "estimated_probability": 0.5,
                "confidence": 0.0,
                "key_factors": ["Unable to research"],
                "sources": []
            }
        
        # Save to database
        self._save_research(market_id, market_title, result)
        
        return result
    
    def _parse_research_response(self, text: str) -> dict:
        """Parse JSON from Claude response"""
        import re
        
        # Try to find JSON in the response
        json_match = re.search(r'\{[^{}]*"summary"[^{}]*\}', text, re.DOTALL)
        
        if json_match:
            try:
                data = json.loads(json_match.group())
                return {
                    "summary": data.get("summary", ""),
                    "estimated_probability": float(data.get("estimated_probability", 0.5)),
                    "confidence": float(data.get("confidence", 0.5)),
                    "key_factors": data.get("key_factors", []),
                    "sources": []
                }
            except json.JSONDecodeError:
                pass
        
        # Fallback: extract what we can
        prob_match = re.search(r'probability["\s:]+(\d\.\d+)', text.lower())
        conf_match = re.search(r'confidence["\s:]+(\d\.\d+)', text.lower())
        
        return {
            "summary": text[:500] if text else "Unable to parse response",
            "estimated_probability": float(prob_match.group(1)) if prob_match else 0.5,
            "confidence": float(conf_match.group(1)) if conf_match else 0.3,
            "key_factors": ["Research result unclear"],
            "sources": []
        }
    
    def _save_research(self, market_id: str, market_title: str, result: dict):
        """Save research result to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO research 
            (market_id, market_title, research_time, summary, 
             estimated_probability, confidence, key_factors, sources)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            market_id,
            market_title,
            datetime.utcnow().isoformat(),
            result.get("summary", ""),
            result.get("estimated_probability", 0.5),
            result.get("confidence", 0.5),
            json.dumps(result.get("key_factors", [])),
            json.dumps(result.get("sources", []))
        ))
        
        conn.commit()
        conn.close()
    
    def delete_research(self, market_id: str):
        """Delete cached research for a market"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM research WHERE market_id = ?", (market_id,))
        conn.commit()
        conn.close()
    
    def list_all_research(self) -> list:
        """List all cached research"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT market_id, market_title, research_time, estimated_probability, confidence FROM research")
        rows = cursor.fetchall()
        conn.close()
        
        return [
            {
                "market_id": row[0],
                "market_title": row[1],
                "research_time": row[2],
                "estimated_probability": row[3],
                "confidence": row[4]
            }
            for row in rows
        ]


# Convenience functions for tool use
_service = None

def get_service() -> ResearchService:
    """Get or create research service singleton"""
    global _service
    if _service is None:
        _service = ResearchService()
    return _service


def get_research_result(market_id: str) -> Optional[dict]:
    """Get cached research result (convenience function)"""
    return get_service().get_research_result(market_id)


def research_market_and_save(
    market_id: str,
    market_title: str,
    focus: str = None
) -> dict:
    """Research market and save (convenience function)"""
    return get_service().research_market_and_save(market_id, market_title, focus)


if __name__ == "__main__":
    print("=" * 60)
    print("Testing Research Module")
    print("=" * 60)
    
    service = ResearchService()
    
    # Test market
    test_market_id = "test_123"
    test_market_title = "Will Bitcoin reach $200,000 by end of 2026?"
    
    print(f"\n[1] Checking cache for: {test_market_id}")
    cached = service.get_research_result(test_market_id)
    print(f"  Cached result: {cached is not None}")
    
    if not cached:
        print(f"\n[2] Researching market: {test_market_title}")
        print("  (This will use web search, may take 10-20 seconds...)")
        
        result = service.research_market_and_save(
            test_market_id,
            test_market_title,
            focus="Focus on recent price predictions and institutional adoption"
        )
        
        print(f"\n  Summary: {result['summary'][:200]}...")
        print(f"  Probability: {result['estimated_probability']:.0%}")
        print(f"  Confidence: {result['confidence']:.0%}")
        print(f"  Key Factors: {result['key_factors']}")
        print(f"  Sources: {len(result['sources'])} found")
    
    print(f"\n[3] Listing all cached research:")
    all_research = service.list_all_research()
    for r in all_research:
        print(f"  - {r['market_title'][:40]}... (prob: {r['estimated_probability']:.0%})")
    
    print("\n" + "=" * 60)
    print("Test complete!")
