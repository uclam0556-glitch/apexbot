import sqlite3
import pandas as pd
import numpy as np
import pickle
import os
import logging
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
import xgboost as xgb

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("ML_Trainer")

DB_PATH = "apex_lite.db"
MODEL_DIR = "models"
MODEL_PATH = os.path.join(MODEL_DIR, "xgb_v1.pkl")

def fetch_data():
    """Fetches and joins trades and feature_store tables."""
    if not os.path.exists(DB_PATH):
        logger.error(f"Database {DB_PATH} not found.")
        return None

    query = """
    SELECT 
        f.regime, 
        f.ultra_score, 
        f.fvg_count, 
        f.btc_rsi, 
        f.funding_rate, 
        f.oi_change, 
        f.fg_index, 
        f.mtf_score, 
        f.cvd_score,
        t.status
    FROM feature_store f
    JOIN trades t ON f.trade_id = t.id
    WHERE t.status IN ('WON', 'LOST')
    """
    
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        logger.error(f"Error fetching data: {e}")
        return None

def train_model():
    """Trains the XGBoost model on historical feature data."""
    logger.info("Starting ML Training Pipeline...")
    
    df = fetch_data()
    if df is None or len(df) == 0:
        logger.warning("No completed trades found in feature_store. Need more data to train.")
        # Generate some mock data so the pipeline doesn't crash on Day 1
        logger.info("Generating mock data to establish the ML Pipeline...")
        df = pd.DataFrame({
            'regime': np.random.choice(['BULL', 'BEAR', 'SIDEWAYS'], 100),
            'ultra_score': np.random.uniform(6.0, 9.5, 100),
            'fvg_count': np.random.randint(0, 5, 100),
            'btc_rsi': np.random.uniform(30, 70, 100),
            'funding_rate': np.random.uniform(-0.05, 0.05, 100),
            'oi_change': np.random.uniform(-5.0, 5.0, 100),
            'fg_index': np.random.uniform(20, 80, 100),
            'mtf_score': np.random.uniform(-8.0, 8.0, 100),
            'cvd_score': np.random.uniform(-10.0, 10.0, 100),
            'status': np.random.choice(['WON', 'LOST'], 100, p=[0.55, 0.45])
        })
    
    logger.info(f"Loaded {len(df)} records for training.")
    
    # Target variable
    y = np.where(df['status'] == 'WON', 1, 0)
    X = df.drop(columns=['status'])
    
    # Preprocessing
    numeric_features = ['ultra_score', 'fvg_count', 'btc_rsi', 'funding_rate', 'oi_change', 'fg_index', 'mtf_score', 'cvd_score']
    categorical_features = ['regime']
    
    numeric_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='median')),
        ('scaler', StandardScaler())
    ])
    
    categorical_transformer = Pipeline(steps=[
        ('imputer', SimpleImputer(strategy='constant', fill_value='UNKNOWN')),
        ('onehot', OneHotEncoder(handle_unknown='ignore'))
    ])
    
    preprocessor = ColumnTransformer(
        transformers=[
            ('num', numeric_transformer, numeric_features),
            ('cat', categorical_transformer, categorical_features)
        ])
    
    # Classifier
    model = xgb.XGBClassifier(
        n_estimators=100, 
        max_depth=3, 
        learning_rate=0.05, 
        eval_metric='logloss',
        use_label_encoder=False
    )
    
    # Pipeline
    clf = Pipeline(steps=[('preprocessor', preprocessor),
                          ('classifier', model)])
    
    # Train-test split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    logger.info("Fitting XGBoost model...")
    clf.fit(X_train, y_train)
    
    train_acc = clf.score(X_train, y_train)
    test_acc = clf.score(X_test, y_test)
    
    logger.info(f"Training complete. Train Accuracy: {train_acc:.2f} | Test Accuracy: {test_acc:.2f}")
    
    # Save model
    os.makedirs(MODEL_DIR, exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)
        
    logger.info(f"Model saved successfully to {MODEL_PATH}.")
    
    # Extract Feature Importances (rough estimation from the pipeline)
    try:
        # Get onehot feature names
        ohe = clf.named_steps['preprocessor'].named_transformers_['cat'].named_steps['onehot']
        cat_features = ohe.get_feature_names_out(categorical_features)
        all_features = numeric_features + list(cat_features)
        
        importances = clf.named_steps['classifier'].feature_importances_
        
        feature_importance = pd.DataFrame({
            'Feature': all_features,
            'Importance': importances
        }).sort_values(by='Importance', ascending=False)
        
        logger.info("\n=== Top Feature Importances ===")
        for idx, row in feature_importance.head(5).iterrows():
            logger.info(f"{row['Feature']}: {row['Importance']:.4f}")
            
    except Exception as e:
        logger.warning(f"Could not extract feature importances: {e}")

if __name__ == "__main__":
    train_model()
