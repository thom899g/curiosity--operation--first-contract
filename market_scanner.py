"""
Multi-source intelligence ingestion and predictive gap detection.
Architectural Choice: Uses established APIs with exponential backoff retries
and TF-IDF vectorization for natural language pattern recognition.
"""

import requests
import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import DBSCAN
from datetime import datetime, timedelta
import time
import logging
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from firebase_init import firebase_manager

logger = logging.getLogger(__name__)

@dataclass
class APIConfig:
    """Configuration for API endpoints with rate limiting"""
    url: str
    headers: Dict[str, str]
    params: Dict[str, Any]
    rate_limit_delay: float  # seconds between requests
    max_retries: int = 3
    timeout: int = 30

class MarketScanner:
    """Orchestrates data collection from multiple sources"""
    
    def __init__(self):
        self.sources = self._initialize_sources()
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words='english',
            ngram_range=(1, 3)  # Capture phrases like "smart contract audit"
        )
        self.clusterer = DBSCAN(
            eps=0.5,
            min_samples=3,
            metric='cosine'
        )
        self.last_request_time = {}
        
    def _initialize_sources(self) -> Dict[str, APIConfig]:
        """Initialize API configurations with realistic endpoints"""
        return {
            "github_trending": APIConfig(
                url="https://api.github.com/search/repositories",
                headers={"Accept": "application/vnd.github.v3+json"},
                params={"q": "created:>2024-01-01", "sort": "stars", "order": "desc", "per_page": 50},
                rate_limit_delay=1.0
            ),
            "stackoverflow": APIConfig(
                url="https://api.stackexchange.com/2.3/questions",
                headers={},
                params={
                    "order": "desc",
                    "sort": "creation",
                    "tagged": "python;javascript;solidity;api",
                    "site": "stackoverflow",
                    "pagesize": 50
                },
                rate_limit_delay=0.5
            ),
            "reddit_programming": APIConfig(
                url="https://www.reddit.com/r/programming/top.json",
                headers={"User-Agent": "AutonomousValueFoundry/1.0"},
                params={"limit": 50, "t": "week"},
                rate_limit_delay=2.0
            ),
            "coinmarketcap_trending": APIConfig(
                url="https://pro-api.coinmarketcap.com/v1/cryptocurrency/trending/latest",
                headers={"X-CMC_PRO_API_KEY": "[REQUIRES_API_KEY]"},
                params={"limit": 20},
                rate_limit_delay=1.5
            )
        }
    
    def _respect_rate_limit(self, source_name: str):
        """Enforce rate limiting between API calls"""
        if source_name in self.last_request_time:
            elapsed = time.time() - self.last_request_time[source_name]
            delay_needed = self.sources[source_name].rate_limit_delay - elapsed
            
            if delay_needed > 0:
                logger.debug(f"Rate limiting: waiting {delay_needed:.2f}s for {source_name}")
                time.sleep(delay_needed)
        
        self.last_request_time[source_name] = time.time()
    
    def fetch_source_data(self, source_name: str) -> Optional[List[Dict]]:
        """Fetch data from a single source with retry logic"""
        if source_name not in self.sources:
            logger.error(f"Unknown source: {source_name}")
            return None
        
        config = self.sources[source_name]
        self._respect_rate_limit(source_name)
        
        for attempt in range(config.max_retries):
            try:
                logger.info(f"Fetching {source_name} (attempt {attempt + 1}/{config.max_retries})")
                
                response = requests.get(
                    config.url,
                    headers=config.headers,
                    params=config.params,
                    timeout=config.timeout
                )
                
                response.raise_for_status()
                data = response.json()
                
                # Source-specific parsing
                if source_name == "github_trending":
                    items = data.get("items", [])
                    parsed = [{
                        "title": item.get("name", ""),
                        "description": item.get("description", ""),
                        "url": item.get("html_url", ""),
                        "stars": item.get("stargazers_count", 0),
                        "language": item.get("language", ""),
                        "topics": item.get("topics", [])
                    } for item in items]
                    
                elif source_name == "stackoverflow":
                    items = data.get("items", [])
                    parsed = [{
                        "title": item.get("title", ""),
                        "body": self._clean_html(item.get("body", "")),
                        "tags": item.get("tags", []),
                        "answer_count": item.get("answer_count", 0),
                        "score": item.get("score", 0),
                        "view_count": item.get("view_count", 0)
                    } for item in items]
                    
                elif source_name == "reddit_programming":
                    items = data.get("data", {}).get("children", [])
                    parsed = [{
                        "title": item.get("data", {}).get("title", ""),
                        "selftext": item.get("data", {}).get("selftext", ""),
                        "score": item.get("data", {}).get("score", 0),
                        "num_comments": item.get("data", {}).get("num_comments", 0),
                        "url": item.get("data", {}).get("url", "")
                    } for item in items]
                
                else:
                    parsed = [{"raw": data}]
                
                logger.info(f"Successfully fetched {len(parsed)} items from {source_name}")
                return parsed
                
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request failed (attempt {attempt + 1}): {str(e)}")
                if attempt < config.max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.info(f"Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Failed to fetch {source_name} after {config.max_retries} attempts")
            except (KeyError, ValueError) as e:
                logger.error(f"Failed to parse {source_name} response: {str(e)}")
                break
        
        return None
    
    def _clean_html(self, text: str) -> str:
        """Basic HTML cleaning for Stack Overflow bodies"""
        if not text:
            return ""
        
        # Remove HTML tags
        text = re.sub(r'<[^>]+>', ' ', text)
        # Remove code blocks markers
        text = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        text = re.sub(r'`[^`]+`', '', text)
        # Normalize whitespace
        text = ' '.join(text.split())
        
        return text[:2000]  # Limit length
    
    def extract_keywords(self, texts: List[str]) -> List[List[str]]:
        """Extract meaningful keywords using TF-IDF"""
        if not texts:
            return []
        
        try:
            # Fit and transform
            tfidf_matrix = self.vectorizer.fit_transform(texts)
            feature_names = self.vectorizer.get_feature_names_out()
            
            # Get top keywords for each document
            all_keywords = []
            
            for i in range(len(texts)):
                row = tfidf_matrix[i]
                top_indices = row.toarray().argsort()[0][-5:]  # Top 5 keywords
                keywords = [feature_names[idx] for idx in top_indices if row[0, idx] > 0.1]
                all_keywords.append(keywords)
            
            return all_keywords
            
        except Exception as e:
            logger.error(f"Keyword extraction failed: {str(e)}")
            return [[] for _ in texts]
    
    def cluster_patterns(self, observations: List[Dict]) -> List[Dict]:
        """Cluster observations to find emerging patterns"""
        if len(observations) < 5:
            logger.warning("Insufficient observations for clustering")
            return []
        
        try:
            # Prepare text for clustering
            texts = [obs.get("title", "") + " " + obs.get("description", "") 
                    for obs in observations]
            
            # Vectorize
            vectors = self.