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

# Generalized system prompt that emphasizes a natural, human-like tone
realtor_role = {
    "role": "system",
    "content": (
        "You are a friendly, professional real estate agent crafting a personalized follow-up email to a prospective buyer. "
        "Write as if you’re speaking naturally, without making the client suspect it’s AI. "
        "When writing your reply:\n"
        "1. Start with a warm greeting using the client’s name, matching their tone.\n"
        "2. Briefly recap what they mentioned (budget, number of bedrooms, backyard preference, neighborhood of interest).\n"
        "3. Ask if they’ve been pre-approved for a mortgage in a casual, conversational way.\n"
        "4. Mention that you’ll check current listings and availability, then let your human colleague know to follow up with details.\n"
        "5. Offer two general time ranges they could consider for a viewing (e.g., \"early next week\" or \"late afternoon\") rather than specific appointments.\n"
        "6. Keep the phrasing natural—include small talk (“Hope you’re doing well,” “Thanks for reaching out,” etc.) and avoid overly structured or robotic language.\n"
        "7. ONLY output the body of the email reply—do not include subject lines, sign-offs, or any extra formatting.\n"
    )
}

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
        index_name=None,
        key_name='uid',
        key_value=uid
    )
    
    if not result or 'Items' not in result:
        return ""
        
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
        email_content = f"Subject: {email.get('subject', '')}\n\nBody: {email.get('body', '')}"
        role = "user" if email.get('type') == 'inbound-email' else "assistant"
        logger.info(f"Email {i+1} - Role: {role}, Subject: {email.get('subject', '')}")
        
        formatted_messages.append({
            "role": role,
            "content": email_content
        })
    
    return formatted_messages

def send_introductory_email(starting_msg, uid):
    """
    Sends a follow-up email when given a single starting message.
    """
    return send_message_to_llm([
        realtor_role,
        {
            "role": "system",
            "content": (
                "ONLY output the body of the email reply—do not include subject lines, signatures, "
                "or any extra formatting."
            )
        },
        {
            "role": "user",
            "content": "Subject: " + starting_msg['subject'] + "\n\nBody: " + starting_msg['body']
        }
    ])

def generate_email_response(emails, uid):
    """
    Generates a follow-up email response based on the provided email chain.
    """
    try:
        if not emails:
            logger.error("Empty email chain provided")
            raise ValueError("Empty email chain")

        logger.info(f"Generating email response for chain of {len(emails)} emails")
        for i, email in enumerate(emails):
            logger.info(f"Input email {i+1}: Subject: {email.get('subject', '')}")

        formatted_messages = format_conversation_for_llm(emails)
        
        formatted_messages.append({
            "role": "system",
            "content": (
                "ONLY output the body of the email reply—do not include subject lines, signatures, "
                "or any extra formatting."
            )
        })
        
        response = send_message_to_llm(formatted_messages)
        logger.info(f"Generated response length: {len(response)}")
        return response
        
    except Exception as e:
        logger.error(f"Error generating email response: {str(e)}")
        raise
