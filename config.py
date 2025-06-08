# config.py
import os

# Load configuration from environment variables
AWS_REGION = os.environ.get('AWS_REGION', 'us-east-2')
TAI_KEY = os.environ['TAI_KEY']  # Together AI API key

# Database Lambda function name
DB_SELECT_LAMBDA = os.environ['DB_SELECT_LAMBDA']  # Single Lambda for all DB operations 

BEDROCK_KB_ID     = os.getenv("BEDROCK_KB_ID")      # your KB’s ID
BEDROCK_MODEL_ARN = os.getenv("BEDROCK_MODEL_ARN")  # e.g. "anthropic.claude-v2:1"
