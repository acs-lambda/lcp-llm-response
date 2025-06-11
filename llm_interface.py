import json
import urllib3
import boto3
import logging
from config import TAI_KEY, AWS_REGION
from typing import Optional, Dict, Any, List
from prompts import PROMPTS
from db import get_user_preferences, store_llm_invocation

# Set up logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize urllib3 pool manager
http = urllib3.PoolManager()

url = "https://api.together.xyz/v1/chat/completions"

class LLMResponder:
    def __init__(self, scenario: str, account_id: Optional[str]):
        original_scenario = scenario
        if scenario not in PROMPTS:
            # Default to continuation_email if unknown scenario
            logger.warning(f"Unknown LLM scenario: '{original_scenario}'. Defaulting to 'continuation_email'.")
            scenario = "continuation_email"
        
        logger.info(f"LLMResponder initialized with scenario: '{scenario}' for account_id: {account_id}")
        self.prompt_config = PROMPTS[scenario]
        self.hyperparameters = self.prompt_config["hyperparameters"]
        self.system_prompt = self.prompt_config["system"]
        self.account_id = account_id
        self.scenario = scenario
        self.model_name = "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"
        
        # For selector_llm or if account_id is None, use base prompt without preferences
        if scenario == "selector_llm" or account_id is None or scenario == "reviewer_llm":
            self.system_prompt = self.prompt_config["system"]
        else:
            # Get user preferences
            user_prefs = get_user_preferences(account_id)
            
            # List to collect preference instructions
            preference_instructions = []
            
            # Add instructions only if preferences are not NULL
            if user_prefs['lcp_tone'] != 'NULL':
                preference_instructions.append(f"IMPORTANT: You MUST write in a {user_prefs['lcp_tone']} tone throughout the entire response")
            
            if user_prefs['lcp_style'] != 'NULL':
                preference_instructions.append(f"IMPORTANT: You MUST use a {user_prefs['lcp_style']} writing style for all content")
            
            if user_prefs['lcp_sample_prompt'] != 'NULL':
                preference_instructions.append(f"IMPORTANT: You MUST closely match the style and tone of this writing sample: {user_prefs['lcp_sample_prompt']}")
            
            # Construct the final system prompt
            if preference_instructions:
                # Start with a strong emphasis on following preferences
                self.system_prompt = "IMPORTANT WRITING PREFERENCES - FOLLOW THESE STRICTLY:\n" + "\n".join(preference_instructions)
                # Add a separator
                self.system_prompt += "\n\n---\n\n"
                # Add the base prompt
                self.system_prompt += self.prompt_config["system"]
            else:
                self.system_prompt = self.prompt_config["system"]

    def format_conversation(self, email_chain: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        messages = [
            {"role": "system", "content": self.system_prompt}
        ]
        for email in email_chain:
            email_content = f"Subject: {email.get('subject', '')}\n\nBody: {email.get('body', '')}"
            role = "user" if email.get('type') == 'inbound-email' else "assistant"
            messages.append({"role": role, "content": email_content})
        return messages

    def send(self, messages: List[Dict[str, str]], conversation_id: Optional[str] = None) -> str:
        headers = {
            "Authorization": f"Bearer {TAI_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model_name,
            "messages": messages,
            **self.hyperparameters,
            "stop": ["<|im_end|>", "<|endoftext|>"],
            "stream": False
        }
        try:
            # Extract scenario from prompt config for better logging
            scenario_name = None
            for name, config in PROMPTS.items():
                if config == self.prompt_config:
                    scenario_name = name
                    break
            
            logger.info(f"Sending request to Together AI API for scenario: '{scenario_name}' with {len(messages)} messages")
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
            
            # Extract token usage from response
            usage = response_data.get("usage", {})
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            
            # Store invocation record if we have an account_id
            if self.account_id:
                store_llm_invocation(
                    associated_account=self.account_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    llm_email_type=self.scenario,
                    model_name=self.model_name,
                    conversation_id=conversation_id
                )
            
            content = response_data["choices"][0]["message"]["content"]
            return content.replace('\\n', '\n')
        except Exception as e:
            logger.error(f"Error in send_message_to_llm: {str(e)}")
            raise

    def generate_response(self, email_chain: List[Dict[str, Any]], conversation_id: Optional[str] = None) -> str:
        messages = self.format_conversation(email_chain)
        return self.send(messages, conversation_id)







def format_conversation_for_llm(email_chain):
    """
    Formats the email chain to be compatible with the LLM input structure.
    Includes both subject and body for each email.
    """
    formatted_messages = [{"role": "system", "content": PROMPTS["intro_email"]["system"]}]
    
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



def update_thread_busy_status(conversation_id: str, busy_value: str) -> bool:
    """
    Updates the thread's busy attribute in DynamoDB.
    Returns True if successful, False otherwise.
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        threads_table = dynamodb.Table('Threads')
        
        threads_table.update_item(
            Key={'conversation_id': conversation_id},
            UpdateExpression='SET busy = :busy',
            ExpressionAttributeValues={':busy': busy_value}
        )
        
        logger.info(f"Successfully updated busy status to {busy_value} for conversation {conversation_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating busy status: {str(e)}")
        return False

def update_thread_flag_for_review(conversation_id: str, flag_value: str) -> bool:
    """
    Updates the thread's flag_for_review attribute in DynamoDB and sets busy to false if flagged.
    Returns True if successful, False otherwise.
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        threads_table = dynamodb.Table('Threads')
        
        # If flagging for review, also set busy to false
        if flag_value == 'true':
            threads_table.update_item(
                Key={'conversation_id': conversation_id},
                UpdateExpression='SET flag_for_review = :flag, busy = :busy',
                ExpressionAttributeValues={
                    ':flag': flag_value,
                    ':busy': 'false'
                }
            )
        else:
            threads_table.update_item(
                Key={'conversation_id': conversation_id},
                UpdateExpression='SET flag_for_review = :flag',
                ExpressionAttributeValues={':flag': flag_value}
            )
        
        logger.info(f"Successfully updated flag_for_review to {flag_value} and busy to {not flag_value == 'true'} for conversation {conversation_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating flag_for_review and busy status: {str(e)}")
        return False

def get_thread_flag_review_override(conversation_id: str) -> Optional[str]:
    """
    Gets the thread's flag_review_override attribute from DynamoDB.
    Returns None if the thread doesn't exist or there's an error.
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        threads_table = dynamodb.Table('Threads')
        
        response = threads_table.get_item(
            Key={'conversation_id': conversation_id},
            ProjectionExpression='flag_review_override'
        )
        
        if 'Item' not in response:
            logger.warning(f"Thread {conversation_id} not found")
            return None
            
        return response['Item'].get('flag_review_override', 'false')
    except Exception as e:
        logger.error(f"Error getting flag_review_override: {str(e)}")
        return None

def update_thread_flag_review_override(conversation_id: str, flag_value: str) -> bool:
    """
    Updates the thread's flag_review_override attribute in DynamoDB.
    Returns True if successful, False otherwise.
    """
    try:
        dynamodb = boto3.resource('dynamodb', region_name=AWS_REGION)
        threads_table = dynamodb.Table('Threads')
        
        threads_table.update_item(
            Key={'conversation_id': conversation_id},
            UpdateExpression='SET flag_review_override = :flag',
            ExpressionAttributeValues={':flag': flag_value}
        )
        
        logger.info(f"Successfully updated flag_review_override to {flag_value} for conversation {conversation_id}")
        return True
    except Exception as e:
        logger.error(f"Error updating flag_review_override: {str(e)}")
        return False

def check_with_reviewer_llm(email_chain: List[Dict[str, Any]], conversation_id: str, account_id: Optional[str] = None) -> bool:
    """
    Uses the reviewer_llm to determine if a conversation needs human review.
    Returns True if the conversation should be flagged for review, False otherwise.
    """
    # First check if review override is enabled
    override_flag = get_thread_flag_review_override(conversation_id)
    if override_flag is None:
        logger.error(f"Could not get flag_review_override for conversation {conversation_id}")
        return True  # Default to flagging for review on error
        
    if override_flag == 'true':
        logger.info(f"Review override enabled for conversation {conversation_id} - skipping reviewer LLM")
        return False  # Skip review since override is enabled
    
    # Create a reviewer LLM instance with account_id if provided
    reviewer = LLMResponder("reviewer_llm", account_id)  # Pass account_id to track invocations
    messages = reviewer.format_conversation(email_chain)
    
    try:
        logger.info("Invoking reviewer LLM to check if conversation needs review...")
        response = reviewer.send(messages, conversation_id)
        decision = response.strip().upper()
        logger.info(f"Reviewer LLM decision: {decision}")
        
        if decision == "FLAG":
            logger.info(f"Thread flagged for review: {conversation_id}")
            if not update_thread_flag_for_review(conversation_id, 'true'):
                logger.error(f"Failed to update flag_for_review for conversation {conversation_id}")
            return True
        elif decision == "CONTINUE":
            return False
        else:
            logger.warning(f"Reviewer LLM returned unknown decision '{decision}', defaulting to FLAG")
            if not update_thread_flag_for_review(conversation_id, 'true'):
                logger.error(f"Failed to update flag_for_review for conversation {conversation_id}")
            return True
    except Exception as e:
        logger.error(f"Error in reviewer LLM: {str(e)}. Defaulting to FLAG.")
        if not update_thread_flag_for_review(conversation_id, 'true'):
            logger.error(f"Failed to update flag_for_review for conversation {conversation_id}")
        return True

def select_scenario_with_llm(email_chain: List[Dict[str, Any]], conversation_id: str, account_id: Optional[str] = None) -> str:
    """
    Uses the selector_llm prompt to classify the email chain and return a scenario keyword.
    """
    # Create a special LLMResponder instance with account_id if provided
    selector = LLMResponder("selector_llm", account_id)  # Pass account_id to track invocations
    messages = selector.format_conversation(email_chain)
    
    try:
        logger.info("Invoking selector LLM to determine scenario...")
        response = selector.send(messages, conversation_id)
        raw_scenario = response.strip()
        logger.info(f"Selector LLM raw response: '{raw_scenario}'")
        
        # Handle scenarios - convert to lowercase for consistency
        scenario = raw_scenario.lower()
        logger.info(f"Normalized scenario: '{scenario}'")
        
        # Validate the scenario is one of the expected email generation scenarios
        valid_scenarios = ["summarizer", "intro_email", "continuation_email", "closing_referral"]
        if scenario in valid_scenarios:
            logger.info(f"Selector LLM chose valid scenario: '{scenario}'")
            return scenario
        else:
            logger.warning(f"Selector LLM returned invalid scenario '{scenario}', defaulting to 'continuation_email'")
            return "continuation_email"
    except Exception as e:
        logger.error(f"Error in selector LLM: {str(e)}. Defaulting to 'continuation_email'.")
        return "continuation_email"

def generate_email_response(emails, uid, conversation_id=None, scenario=None):
    """
    Generates a follow-up email response based on the provided email chain and scenario.
    If scenario is None, uses the reviewer LLM first, then the selector LLM to determine the scenario.
    """
    try:
        logger.info(f"Starting email generation for conversation_id: {conversation_id}, uid: {uid}")
        logger.info(f"Initial scenario provided: {scenario}")
        
        # 1) First check with reviewer LLM if conversation needs review (only if no scenario is forced)
        if conversation_id and scenario is None:
            logger.info("No scenario provided - checking with reviewer LLM first...")
            if check_with_reviewer_llm(emails, conversation_id, uid):  # Pass uid to reviewer LLM
                # If flagged for review, return None to prevent email sending
                logger.info(f"Conversation {conversation_id} flagged for review - no email will be sent")
                return None
        
        # 2) Determine scenario (intro vs continuation/etc.)
        if not emails:
            scenario = "intro_email"
            logger.info("No emails provided, forcing 'intro_email' scenario")
        elif scenario is None:
            logger.info("No scenario provided - using selector LLM to determine scenario...")
            scenario = select_scenario_with_llm(emails, conversation_id, uid)  # Pass uid to selector LLM
            logger.info(f"Selector LLM determined scenario: '{scenario}'")
        else:
            logger.info(f"Using provided scenario: '{scenario}'")
  
        # 3) Generate response using the determined scenario
        logger.info(f"Creating LLMResponder for scenario: '{scenario}'")
        responder = LLMResponder(scenario, uid)  # Pass uid to get user preferences
        
        logger.info(f"Generating email response using '{scenario}' scenario...")
        response = responder.generate_response(emails, conversation_id)
        logger.info(f"Successfully generated response for scenario '{scenario}' - length: {len(response)}")
        
        return response
    except Exception as e:
        logger.error(f"Error generating email response: {str(e)}")
        raise
