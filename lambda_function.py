import json
import boto3
import logging
import uuid
from typing import Dict, Any, List, Tuple, Optional

from llm_interface import generate_email_response, format_conversation_for_llm
from db import get_email_chain, check_rate_limit, update_invocation_count
from config import logger, AUTH_BP

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

def generate_response_for_conversation(conversation_id: str, account_id: str, invocation_id: str, is_first_email: bool = False, scenario: str = None) -> Dict[str, Any]:
    """
    Generates an LLM response for a conversation.
    Returns the generated response and status.
    
    Args:
        conversation_id: The conversation ID
        account_id: The account ID  
        invocation_id: Unique ID for this Lambda invocation (groups all LLM calls)
        is_first_email: Whether this is the first email in a chain
        scenario: Optional scenario override
    """
    try:
        logger.info(f"Starting response generation for invocation {invocation_id}")
        logger.info(f"  - Conversation: {conversation_id}")
        logger.info(f"  - Account: {account_id}")
        logger.info(f"  - Is first email: {is_first_email}")
        logger.info(f"  - Scenario: {scenario}")
        
        # Get the email chain
        chain = get_email_chain(conversation_id)
        
        if not chain:
            raise ValueError("Could not get email chain")

        # For first emails, we only use the first message
        if is_first_email:
            chain = [chain[0]]

        # Generate response with invocation_id for tracking
        response = generate_email_response(chain, account_id, conversation_id, scenario, invocation_id)
        logger.info(f"Generated response for conversation {conversation_id} using scenario '{scenario}' (invocation: {invocation_id})")

        # If response is None, it means the conversation was flagged for review
        if response is None:
            return {
                'response': None,
                'conversation_id': conversation_id,
                'invocation_id': invocation_id,
                'status': 'flagged_for_review',
                'message': 'Conversation flagged for human review - no email will be sent'
            }

        # Map scenario to llm_email_type
        llm_email_type = scenario if scenario in ['intro_email', 'continuation_email', 'closing_referral', 'summarizer'] else 'continuation_email'

        return {
            'response': response,
            'conversation_id': conversation_id,
            'invocation_id': invocation_id,
            'status': 'success',
            'llm_email_type': llm_email_type
        }
    except Exception as e:
        logger.error(f"Error generating response for invocation {invocation_id}: {str(e)}")
        return {
            'status': 'error',
            'error': str(e),
            'invocation_id': invocation_id
        }

def check_and_update_rate_limits(account_id: str) -> Tuple[bool, Optional[str]]:
    """
    Check AWS rate limit and update if allowed.
    Returns (is_allowed, error_message)
    """
    # Check AWS rate limit
    is_allowed, error_msg = check_rate_limit('RL_AWS', account_id, 'aws')
    if not is_allowed:
        return False, error_msg
        
    # Update AWS invocation count
    if not update_invocation_count('RL_AWS', account_id):
        return False, "Failed to update AWS invocation count"
        
    return True, None

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda handler for LLM response generation.
    Expected event format:
    {
        'conversation_id': str,
        'account_id': str,
        'is_first_email': bool (optional),
        'scenario': str (optional)
    }
    """
    # Generate unique invocation ID at the start to group all LLM calls in this Lambda invocation
    invocation_id = str(uuid.uuid4())
    logger.info(f"=== LAMBDA INVOCATION START ===")
    logger.info(f"Invocation ID: {invocation_id}")
    logger.info(f"Lambda request ID: {getattr(context, 'aws_request_id', 'unknown')}")
    
    conv_id = None
    acc_id = None
    is_first = False
    scenario = None
    session_id = None
    try:
        # Validate input
        required_fields = ['conversation_id', 'account_id']
        for field in required_fields:
            if field not in event:
                if 'body' in event:
                    # convert string to dict
                    event['body'] = json.loads(event['body'])
                    if 'conversation_id' not in event['body']:
                        raise ValueError("Missing required field: conversation_id")
                    if 'account_id' not in event['body']:
                        raise ValueError("Missing required field: account_id")
                    if 'is_first_email' in event['body']:
                        is_first = event['body']['is_first_email']
                    if 'scenario' in event['body']:
                        scenario = event['body']['scenario']
                    if 'session_id' in event['body']:
                        session_id = event['body']['session_id']
                    # parse conv_id and acc_id
                    conv_id = event['body']['conversation_id']
                    acc_id = event['body']['account_id']
                    break
                else:
                    raise ValueError("Missing required field: conversation_id or account_id")
            else:
                conv_id = event['conversation_id']
                acc_id = event['account_id']
                if 'is_first_email' in event:
                    is_first = event['is_first_email']
                if 'scenario' in event:
                    scenario = event['scenario']
                if 'session_id' in event:
                    session_id = event['session_id']

        # Check authorization and rate limits if not using AUTH_BP
        if session_id != AUTH_BP:
            authorize(acc_id, session_id)
            # Check AWS rate limit before proceeding
            is_allowed, error_msg = check_and_update_rate_limits(acc_id)
            if not is_allowed:
                logger.warning(f"Rate limit exceeded for account {acc_id}: {error_msg}")
                return {
                    'statusCode': 429,
                    'body': json.dumps({
                        'status': 'error',
                        'error': error_msg,
                        'invocation_id': invocation_id
                    })
                }
        
        # Generate response
        result = generate_response_for_conversation(
            conv_id,
            acc_id,
            invocation_id,
            is_first,
            scenario
        )

        logger.info(f"=== LAMBDA INVOCATION END ===")
        logger.info(f"Invocation ID: {invocation_id}")
        logger.info(f"Result status: {result.get('status', 'unknown')}")
        
        return {
            'statusCode': 200 if result['status'] == 'success' else 500,
            'body': json.dumps(result)
        }
    except Exception as e:
        logger.error(f"Error in lambda handler for invocation {invocation_id}: {str(e)}")
        logger.error(f"=== LAMBDA INVOCATION FAILED ===")
        logger.error(f"Invocation ID: {invocation_id}")
        return {
            'statusCode': 500,
            'body': json.dumps({
                'status': 'error',
                'error': str(e),
                'invocation_id': invocation_id
            })
        } 