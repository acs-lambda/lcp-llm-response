# db.py
import json
import boto3
import logging
from typing import Dict, Any, Optional, List
from config import AWS_REGION, DB_SELECT_LAMBDA

logger = logging.getLogger()
logger.setLevel(logging.INFO)

lambda_client = boto3.client('lambda', region_name=AWS_REGION)

def invoke_db_select(table_name: str, index_name: Optional[str], key_name: str, key_value: Any) -> Optional[List[Dict[str, Any]]]:
    """
    Generic function to invoke the db-select Lambda for read operations only.
    Returns a list of items or None if the invocation failed.
    """
    try:
        payload = {
            'table_name': table_name,
            'index_name': index_name,
            'key_name': key_name,
            'key_value': key_value
        }
        
        response = lambda_client.invoke(
            FunctionName=DB_SELECT_LAMBDA,
            InvocationType='RequestResponse',
            Payload=json.dumps(payload)
        )
        
        response_payload = json.loads(response['Payload'].read())
        if response_payload['statusCode'] != 200:
            logger.error(f"Database Lambda failed: {response_payload}")
            return None
            
        result = json.loads(response_payload['body'])
        logger.info(f"Database Lambda response: {result}")
        return result if isinstance(result, list) else None
    except Exception as e:
        logger.error(f"Error invoking database Lambda: {str(e)}")
        return None

def get_conversation_id(message_id: str) -> Optional[str]:
    """Get conversation ID by message ID."""
    if not message_id:
        return None
    
    result = invoke_db_select(
        table_name='Conversations',
        index_name='response_id-index',
        key_name='response_id',
        key_value=message_id
    )
    
    # Handle list response
    if isinstance(result, list) and result:
        return result[0].get('conversation_id')
    return None

def get_associated_account(email: str) -> Optional[str]:
    """Get account ID by email."""
    result = invoke_db_select(
        table_name='Users',
        index_name='responseEmail-index',
        key_name='responseEmail',
        key_value=email.lower()
    )
    
    # Handle list response
    if isinstance(result, list) and result:
        return result[0].get('id')
    return None

def get_email_chain(conversation_id: str) -> List[Dict[str, Any]]:
    """Get email chain for a conversation."""
    result = invoke_db_select(
        table_name='Conversations',
        index_name='conversation_id-index',
        key_name='conversation_id',
        key_value=conversation_id
    )
    
    # Handle list response directly
    if not isinstance(result, list):
        return []
        
    # Sort by timestamp and format items
    sorted_items = sorted(result, key=lambda x: x.get('timestamp', ''))
    
    return [{
        'subject': item.get('subject', ''),
        'body': item.get('body', ''),
        'sender': item.get('sender', ''),
        'timestamp': item.get('timestamp', ''),
        'type': item.get('type', '')
    } for item in sorted_items]

def get_account_email(account_id: str) -> Optional[str]:
    """Get account email by account ID."""
    result = invoke_db_select(
        table_name='Users',
        index_name="id-index",
        key_name='id',
        key_value=account_id
    )
    
    # Handle list response
    if isinstance(result, list) and result:
        return result[0].get('responseEmail')
    return None 