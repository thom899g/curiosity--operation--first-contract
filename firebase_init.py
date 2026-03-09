"""
Firebase Admin SDK initialization and Firestore schema management.
Critical: Central state management for entire Autonomous Value Foundry.
Architectural Choice: Firebase provides real-time sync, offline capabilities, 
and scales to millions of documents without server management.
"""

import firebase_admin
from firebase_admin import credentials, firestore, initialize_app
from google.cloud.firestore_v1.base_query import FieldFilter
import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)

class FirebaseManager:
    """Singleton manager for Firebase operations with error recovery"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FirebaseManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self.db = None
            self.initialized = False
            self._schema_versions = {}
            self._initialized = True
    
    def initialize(self, credential_path: str = "firebase_credentials.json") -> bool:
        """Initialize Firebase with automatic credential discovery"""
        try:
            # Check for environment variable first (for deployment)
            if os.getenv("FIREBASE_CREDENTIALS_JSON"):
                cred_dict = json.loads(os.getenv("FIREBASE_CREDENTIALS_JSON"))
                cred = credentials.Certificate(cred_dict)
                logger.info("Loaded Firebase credentials from environment")
            elif os.path.exists(credential_path):
                cred = credentials.Certificate(credential_path)
                logger.info(f"Loaded Firebase credentials from {credential_path}")
            else:
                logger.error("No Firebase credentials found. Requesting human intervention.")
                self._request_credentials()
                return False
            
            # Initialize with error handling for duplicate apps
            try:
                firebase_admin.get_app()
                logger.warning("Firebase app already exists, using existing instance")
            except ValueError:
                firebase_admin.initialize_app(cred)
                logger.info("Firebase app initialized successfully")
            
            self.db = firestore.client()
            self.initialized = True
            
            # Initialize collections with schema validation
            self._initialize_schemas()
            
            logger.info(f"Firebase connected to project: {cred.project_id}")
            return True
            
        except Exception as e:
            logger.error(f"Firebase initialization failed: {str(e)}", exc_info=True)
            self.initialized = False
            return False
    
    def _request_credentials(self):
        """Request Firebase credentials via human protocol"""
        request_msg = """
        **NECESSITY**: Firebase required for state management across all Autonomous Value Foundry operations
        **FREE ALTERNATIVES**: None - Firebase provides real-time sync, offline support, and scales automatically
        **ACTION NEEDED**:
          1. Go to https://console.firebase.google.com/
          2. Create project 'autonomous-value-foundry'
          3. Generate service account key (Settings > Service Accounts > Generate New Private Key)
          4. Download JSON and provide via TELEGRAM or store as 'firebase_credentials.json'
        """
        logger.critical(request_msg)
    
    def _initialize_schemas(self):
        """Initialize Firestore collections with validation rules"""
        core_schemas = {
            "raw_observations": {
                "description": "Raw data from intelligence sources",
                "fields": ["source", "raw_text", "extracted_keywords", "sentiment_score", 
                          "predicted_market_gap", "timestamp", "metadata"]
            },
            "emerging_patterns": {
                "description": "Clustered problem patterns with market analysis",
                "fields": ["pattern_hash", "frequency_score", "estimated_market_size", 
                          "first_observed", "last_observed", "sample_problems", "confidence"]
            },
            "solution_arenas": {
                "description": "GAA competition results for specific problems",
                "fields": ["problem_id", "architect_proposals", "critique_reports", 
                          "final_synthesis", "created_at", "updated_at"]
            },
            "treasury_allocations": {
                "description": "Capital allocation and yield tracking",
                "fields": ["date", "total_value", "by_asset", "yield_generated", 
                          "risk_metrics", "rebalance_log"]
            }
        }
        
        # Create collection references and validate
        for collection_name, schema in core_schemas.items():
            try:
                # Test write/read to ensure collection exists and permissions work
                test_doc = self.db.collection(collection_name).document("_schema_test")
                test_doc.set({
                    "schema_version": "1.0",
                    "fields": schema["fields"],
                    "created": datetime.now(),
                    "description": schema["description"]
                }, merge=True)
                test_doc.delete()  # Clean up test
                
                self._schema_versions[collection_name] = "1.0"
                logger.info(f"Validated collection: {collection_name}")
                
            except Exception as e:
                logger.error(f"Failed to initialize collection {collection_name}: {str(e)}")
                raise
    
    def log_observation(self, source: str, raw_text: str, metadata: Dict[str, Any] = None) -> str:
        """Log raw observation with automatic timestamping"""
        if not self.initialized:
            logger.error("Firebase not initialized")
            return ""
        
        try:
            doc_ref = self.db.collection("raw_observations").document()
            
            observation = {
                "source": source,
                "raw_text": raw_text[:10000],  # Limit size
                "extracted_keywords": [],
                "sentiment_score": 0.0,
                "predicted_market_gap": "",
                "timestamp": datetime.now(),
                "metadata": metadata or {},
                "processed": False
            }
            
            doc_ref.set(observation)
            logger.info(f"Logged observation from {source} with ID: {doc_ref.id}")
            return doc_ref.id
            
        except Exception as e:
            logger.error(f"Failed to log observation: {str(e)}")
            return ""
    
    def get_unprocessed_observations(self, limit: int = 100) -> List[Dict]:
        """Retrieve unprocessed observations for analysis"""
        if not self.initialized:
            return []
        
        try:
            query = (self.db.collection("raw_observations")
                    .where(filter=FieldFilter("processed", "==", False))
                    .limit(limit))
            
            docs = query.stream()
            observations = []
            
            for doc in docs:
                data = doc.to_dict()
                data["id"] = doc.id
                observations.append(data)
            
            logger.info(f"Retrieved {len(observations)} unprocessed observations")
            return observations
            
        except Exception as e:
            logger.error(f"Failed to retrieve observations: {str(e)}")
            return []

# Singleton instance for import
firebase_manager = FirebaseManager()

def init_firebase():
    """Public initialization function"""
    return firebase_manager.initialize()