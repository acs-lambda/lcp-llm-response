import json
import urllib3
import boto3
import logging
from config import TAI_KEY, AWS_REGION, DB_SELECT_LAMBDA
from typing import Optional, Dict, Any

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize urllib3 pool manager
http = urllib3.PoolManager()

url = "https://api.together.xyz/v1/chat/completions"

dynamodb = boto3.client('dynamodb', region_name=AWS_REGION)
dynamodb_resource = boto3.resource('dynamodb', region_name=AWS_REGION)
lambda_client = boto3.client('lambda', region_name=AWS_REGION)

realtor_role = {"role": "system", "content": "You are a real estate agent. Respond as if you are trying to follow up to this potential client. This email was auto-generated and a personalized response to follow up with the user should be sent based on the contents given to you. Remember to respond as the agent and not the client. You are answering whatever questions the client asks."}

def invoke_db_select(table_name: str, index_name: Optional[str], key_name: str, key_value: Any) -> Optional[Dict[str, Any]]:
    """
    Generic function to invoke the db-select Lambda for read operations only.
    Returns the parsed response or None if the invocation failed.
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
            
        return json.loads(response_payload['body'])
    except Exception as e:
        logger.error(f"Error invoking database Lambda: {str(e)}")
        return None

def get_template_to_use(uid: str, email_type: str) -> str:
    """Get template content using db-select."""
    result = invoke_db_select(
        table_name='Templates',
        index_name=None,  # Primary key query
        key_name='uid',
        key_value=uid
    )
    
    if not result or 'Items' not in result:
        return ""
        
    # Find the first activated template of the specified type
    for item in result['Items']:
        if item.get('activated') and item.get('email_type') == email_type:
            return item.get('content', '')
            
    return ""

def send_message_to_llm(messages):
    """
    Sends messages to the LLM API and returns the response using urllib3.
    """
    headers = {
        "Authorization": f"Bearer {TAI_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7,
        "top_p": 0.7,
        "top_k": 50,
        "repetition_penalty": 1,
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "stream": False
    }
    
    try:
        logger.info("Sending request to Together AI API")
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request(
            'POST',
            url,
            body=encoded_data,
            headers=headers
        )
        
        if response.status != 200:
            logger.error(f"API call failed with status {response.status}: {response.data.decode('utf-8')}")
            raise Exception(f"Failed to fetch response from Together AI API: {response.data.decode('utf-8')}")

        response_data = json.loads(response.data.decode('utf-8'))
        if "choices" not in response_data:
            logger.error(f"Invalid API response format: {response_data}")
            raise Exception("Invalid response format from Together AI API")

        return response_data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Error in send_message_to_llm: {str(e)}")
        raise

def format_conversation_for_llm(email_chain):
    """
    Formats the email chain to be compatible with the LLM input structure.
    Includes both subject and body for each email.
    """
    formatted_messages = [realtor_role]
    
    logger.info(f"Formatting conversation for LLM. Chain length: {len(email_chain)}")
    for i, email in enumerate(email_chain):
        # Format each email with both subject and body
        email_content = f"Subject: {email.get('subject', '')}\n\nBody: {email.get('body', '')}"
        role = "user" if email.get('type') == 'inbound-email' else "assistant"
        logger.info(f"Email {i+1} - Role: {role}, Subject: {email.get('subject', '')}, Body length: {len(email.get('body', ''))}")
        
        formatted_messages.append({
            "role": role,
            "content": email_content
        })
    
    return formatted_messages


def send_introductory_email(starting_msg, uid):
    # template = get_template_to_use(uid)
    return send_message_to_llm([
        realtor_role,
        {"role": "system", "content": "ONLY output the body of the email reply. Do NOT include the subject, signature, closing, sender name, or any extra text. Only the main message body as you would write it in the email editor."},
        {"role": "user", "content": "Subject: " +starting_msg['subject'] +"\n\nBody:" + starting_msg['body']}
    ])

def generate_email_response(emails, uid):
    """
    Generates an email response based on the email chain.
    Handles both first-time and subsequent emails consistently.
    """
    try:
        if not emails:
            logger.error("Empty email chain provided")
            raise ValueError("Empty email chain")

        logger.info(f"Generating email response for chain of {len(emails)} emails")
        for i, email in enumerate(emails):
            logger.info(f"Input email {i+1}:")
            logger.info(f"  Subject: {email.get('subject', '')}")
            logger.info(f"  Body: {email.get('body', '')[:100]}...")  # First 100 chars
            logger.info(f"  Type: {email.get('type', 'unknown')}")

        # Format the conversation for the LLM
        formatted_messages = format_conversation_for_llm(emails)
        
        # Add the system message for consistent formatting
        formatted_messages.append({
            "role": "system",
            "content": "ONLY output the body of the email reply. Do NOT include the subject, signature, closing, sender name, or any extra text. Only the main message body as you would write it in the email editor."
        })
        
        # Get response from LLM
        response = send_message_to_llm(formatted_messages)
        logger.info(f"Generated response length: {len(response)}")
        logger.info(f"Response preview: {response[:100]}...")  # First 100 chars
        return response
        
    except Exception as e:
        logger.error(f"Error generating email response: {str(e)}")
        raise 