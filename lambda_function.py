import json
import boto3
import logging
from typing import Dict, Any, List

from llm_interface import generate_email_response, format_conversation_for_llm
from db import get_email_chain

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def generate_response_for_conversation(conversation_id: str, account_id: str, is_first_email: bool = False) -> Dict[str, Any]:
    """
    Generates an LLM response for a conversation.
    Returns the generated response and status.
    """
    try:
        # Get the email chain
        chain = get_email_chain(conversation_id)
        
        if not chain:
            raise ValueError("Could not get email chain")

        # For first emails, we only use the first message
        if is_first_email:
            chain = [chain[0]]

        # Generate response
        response = generate_email_response(chain, account_id)
        logger.info(f"Generated response for conversation {conversation_id}")

        return {
            'response': response,
            'conversation_id': conversation_id,
            'status': 'success'
        }
    except Exception as e:
        logger.error(f"Error generating response: {str(e)}")
        return {
            'status': 'error',
            'error': str(e)
        }

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for LLM response generation.
    Expected event format:
    {
        'conversation_id': str,
        'account_id': str,
        'is_first_email': bool (optional)
    }
    """
    try:
        # Validate input
        required_fields = ['conversation_id', 'account_id']
        for field in required_fields:
            if field not in event:
                raise ValueError(f"Missing required field: {field}")

        # Generate response
        result = generate_response_for_conversation(
            event['conversation_id'],
            event['account_id'],
            event.get('is_first_email', False)
        )

        return {
            'statusCode': 200 if result['status'] == 'success' else 500,
            'body': json.dumps(result)
        }
    except Exception as e:
        logger.error(f"Error in lambda handler: {str(e)}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'error',
                'error': str(e)
            })
        } 