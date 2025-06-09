import json
import urllib3
import boto3
import logging
from config import TAI_KEY, AWS_REGION, DB_SELECT_LAMBDA
from typing import Optional, Dict, Any, List
from prompts import PROMPTS

from config import BEDROCK_KB_ID, BEDROCK_MODEL_ARN  # new

# Initialize Bedrock retrieval+generation client
bedrock_client = boto3.client(
    "bedrock-agent-runtime",
    region_name=AWS_REGION
)


# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize urllib3 pool manager
http = urllib3.PoolManager()

url = "https://api.together.xyz/v1/chat/completions"

dynamodb = boto3.client('dynamodb', region_name=AWS_REGION)
dynamodb_resource = boto3.resource('dynamodb', region_name=AWS_REGION)
lambda_client = boto3.client('lambda', region_name=AWS_REGION)

class LLMResponder:
    def __init__(self, scenario: str):
        if scenario not in PROMPTS:
            # Default to continuation_email if unknown scenario
            scenario = "continuation_email"
            logger.warning(f"Unknown LLM scenario: {scenario}. Defaulting to 'continuation_email'.")
        self.prompt_config = PROMPTS[scenario]
        self.system_prompt = self.prompt_config["system"]
        self.hyperparameters = self.prompt_config["hyperparameters"]

    def format_conversation(self, email_chain: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        for email in email_chain:
            email_content = f"Subject: {email.get('subject', '')}\n\nBody: {email.get('body', '')}"
            role = "user" if email.get('type') == 'inbound-email' else "assistant"
            messages.append({"role": role, "content": email_content})
        return messages

    def send(self, messages: List[Dict[str, str]]) -> str:
        headers = {
            "Authorization": f"Bearer {TAI_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
            "messages": messages,
            **self.hyperparameters,
            "stop": ["<|im_end|>", "<|endoftext|>"],
            "stream": False
        }
        try:
            logger.info(f"Sending request to Together AI API for scenario: {self.system_prompt[:40]}...")
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
            content = response_data["choices"][0]["message"]["content"]
            return content.replace('\\n', '\n')
        except Exception as e:
            logger.error(f"Error in send_message_to_llm: {str(e)}")
            raise

    def generate_response(self, email_chain: List[Dict[str, Any]]) -> str:
        messages = self.format_conversation(email_chain)
        return self.send(messages)

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

        content = response_data["choices"][0]["message"]["content"]
        return content.replace('\\n', '\n')
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
                "or any extra formatting. Use \\n to insert line breaks as needed."
            )
        },
        {
            "role": "user",
            "content": "Subject: " + starting_msg['subject'] + "\n\nBody: " + starting_msg['body']
        }
    ])

def update_thread_flag_for_review(conversation_id: str, flag_value: bool) -> bool:
    """
    Updates the thread's flag_for_review attribute in DynamoDB.
    Returns True if successful, False otherwise.
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        threads_table = dynamodb.Table('Threads')
        
        threads_table.update_item(
            Key={'conversation_id': conversation_id},
            UpdateExpression='SET flag_for_review = :flag',
            ExpressionAttributeValues={':flag': flag_value}
        )
        
        logger.info(f"Successfully updated flag_for_review to {flag_value} for conversation {conversation_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating flag_for_review: {str(e)}")
        return False

def select_scenario_with_llm(email_chain: List[Dict[str, Any]], conversation_id: str) -> str:
    """
    Uses the selector_llm prompt to classify the email chain and return a scenario keyword.
    If FLAG is returned, updates the thread's flag_for_review attribute.
    """
    selector_prompt = {
        "role": "system",
        "content": PROMPTS["selector_llm"]["system"]
    }
    formatted_chain = []
    for email in email_chain:
        email_content = f"Subject: {email.get('subject', '')}\n\nBody: {email.get('body', '')}"
        role = "user" if email.get('type') == 'inbound-email' else "assistant"
        formatted_chain.append({"role": role, "content": email_content})
    messages = [selector_prompt] + formatted_chain
    headers = {
        "Authorization": f"Bearer {TAI_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "messages": messages,
        **PROMPTS["selector_llm"]["hyperparameters"],
        "stop": ["<|im_end|>", "<|endoftext|>"],
        "stream": False
    }
    try:
        logger.info("Invoking selector LLM to determine scenario...")
        encoded_data = json.dumps(payload).encode('utf-8')
        response = http.request(
            'POST',
            url,
            body=encoded_data,
            headers=headers
        )
        if response.status != 200:
            logger.error(f"Selector LLM call failed: {response.data.decode('utf-8')}")
            raise Exception(f"Selector LLM failed: {response.data.decode('utf-8')}")
        response_data = json.loads(response.data.decode('utf-8'))
        if "choices" not in response_data:
            logger.error(f"Invalid selector LLM response: {response_data}")
            raise Exception("Invalid selector LLM response")
        scenario = response_data["choices"][0]["message"]["content"].strip().upper()
        logger.info(f"Selector LLM chose scenario: {scenario}")
        
        # Handle FLAG response
        if scenario == "FLAG":
            logger.info(f"Thread flagged for review: {conversation_id}")
            if not update_thread_flag_for_review(conversation_id, True):
                logger.error(f"Failed to update flag_for_review for conversation {conversation_id}")
            return "continuation_email"  # Default to continuation_email after flagging
        
        # Handle other scenarios
        scenario = scenario.lower()
        if scenario not in PROMPTS or scenario == "selector_llm":
            logger.warning(f"Selector LLM returned unknown scenario '{scenario}', defaulting to 'continuation_email'")
            return "continuation_email"
        return scenario
    except Exception as e:
        logger.error(f"Error in selector LLM: {str(e)}. Defaulting to 'continuation_email'.")
        return "continuation_email"

# --- Backwards-compatible API for lambda_function.py ---
def generate_email_response(emails, uid, conversation_id=None, scenario=None):
    """
    Generates a follow-up email response based on the provided email chain and scenario.
    If scenario is None, uses the selector LLM to determine the scenario.
    """
    try:
        # 1) Determine scenario (intro vs continuation/etc.)
        if not emails:
            scenario = "intro_email"
            logger.info("No emails provided, forcing 'intro_email' scenario")
        elif scenario is None:
            scenario = select_scenario_with_llm(emails, conversation_id)
  
        logger.info(f"Generating email response for a '{scenario}' scenario via LLMResponder")
        responder = LLMResponder(scenario)
        response = responder.generate_response(emails)
        logger.info(f"Generated response length: {len(response)}")
        return response
    except Exception as e:
        logger.error(f"Error generating email response: {str(e)}")
        raise
