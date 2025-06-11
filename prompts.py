import boto3
import logging
from typing import Dict, Any, Optional
from config import AWS_REGION

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Model mapping for each LLM type - easily change models here
MODEL_MAPPING = {
    "summarizer": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "intro_email": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free", 
    "continuation_email": "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
    "closing_referral": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    "selector_llm": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",  # Fast classification task
    "reviewer_llm": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8"   # Fast review task
}

# Import the db functionality for getting user preferences
def invoke_db_select(table_name: str, index_name: Optional[str], key_name: str, key_value: Any) -> Optional[list]:
    """
    Generic function to invoke the db-select Lambda for read operations only.
    Returns a list of items or None if the invocation failed.
    """
    try:
        import json
        from config import DB_SELECT_LAMBDA
        
        lambda_client = boto3.client('lambda', region_name=AWS_REGION)
        
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

def get_user_tone(account_id: str) -> str:
    """Get user's tone preference by account ID."""
    if not account_id:
        return 'NULL'
    
    result = invoke_db_select(
        table_name='Users',
        index_name="id-index",
        key_name='id',
        key_value=account_id
    )
    
    if isinstance(result, list) and result:
        tone = result[0].get('lcp_tone', 'NULL')
        return tone if tone != 'NULL' else 'NULL'
    return 'NULL'

def get_user_style(account_id: str) -> str:
    """Get user's writing style preference by account ID."""
    if not account_id:
        return 'NULL'
    
    result = invoke_db_select(
        table_name='Users',
        index_name="id-index",
        key_name='id',
        key_value=account_id
    )
    
    if isinstance(result, list) and result:
        style = result[0].get('lcp_style', 'NULL')
        return style if style != 'NULL' else 'NULL'
    return 'NULL'

def get_user_sample_prompt(account_id: str) -> str:
    """Get user's sample prompt preference by account ID."""
    if not account_id:
        return 'NULL'
    
    result = invoke_db_select(
        table_name='Users',
        index_name="id-index",
        key_name='id',
        key_value=account_id
    )
    
    if isinstance(result, list) and result:
        sample = result[0].get('lcp_sample_prompt', 'NULL')
        return sample if sample != 'NULL' else 'NULL'
    return 'NULL'

def get_user_location_data(account_id: str) -> Dict[str, str]:
    """Get user's location data by account ID."""
    if not account_id:
        return {}
    
    result = invoke_db_select(
        table_name='Users',
        index_name="id-index",
        key_name='id',
        key_value=account_id
    )
    
    if isinstance(result, list) and result:
        user_data = result[0]
        return {
            'location': user_data.get('location', ''),
            'state': user_data.get('state', ''),
            'country': user_data.get('country', ''),
            'zipcode': user_data.get('zipcode', ''),
            'bio': user_data.get('bio', '')
        }
    return {}

def construct_realtor_bio(location_data: Dict[str, str]) -> str:
    """Construct a realtor bio from location data and bio."""
    if not location_data:
        return ""
    
    bio_parts = []
    
    # Add location context
    location_info = []
    if location_data.get('location'):
        location_info.append(location_data['location'])
    if location_data.get('state'):
        location_info.append(location_data['state'])
    if location_data.get('country') and location_data['country'].lower() != 'united states':
        location_info.append(location_data['country'])
    
    location_context = ""
    if location_info:
        location_str = ", ".join(location_info)
        if location_data.get('zipcode'):
            location_str += f" ({location_data['zipcode']})"
        location_context = f"You specialize in the {location_str} market. "
    
    # Build the complete bio instruction
    if location_data.get('bio'):
        if location_context:
            return f"The realtor you are emulating wrote this bio: \"{location_data['bio']}\" {location_context}Use this information to inform your responses and maintain consistency with their professional identity. "
        else:
            return f"The realtor you are emulating wrote this bio: \"{location_data['bio']}\" Use this information to inform your responses and maintain consistency with their professional identity. "
    elif location_context:
        return f"You are a local real estate expert. {location_context}Use your market expertise to inform your responses. "
    
    return ""



def get_prompts(account_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Get the prompts dictionary with user preferences and realtor bio embedded directly into the system prompts.
    For scenarios that don't use preferences (selector_llm, reviewer_llm), account_id is ignored.
    """
    
    # Get user preferences and bio if account_id provided
    tone = ""
    style = ""
    sample_instruction = ""
    realtor_bio = ""
    
    if account_id and account_id not in ['selector_llm', 'reviewer_llm']:
        user_tone = get_user_tone(account_id)
        user_style = get_user_style(account_id)
        user_sample = get_user_sample_prompt(account_id)
        location_data = get_user_location_data(account_id)
        
        if user_tone != 'NULL':
            tone = f" in a {user_tone} tone"
        
        if user_style != 'NULL':
            style = f" using a {user_style} writing style"
        
        if user_sample != 'NULL':
            sample_instruction = f" that closely matches the style and tone of this writing sample: {user_sample}"
        
        realtor_bio = construct_realtor_bio(location_data)
    
    return {
        "summarizer": {
            "system": f"You are an expert summarizer for real estate email threads. {realtor_bio}Write your summary{tone}{style}{sample_instruction}.\n\nFocus only on extracting true key points, client intent, and concrete action items. Do NOT add, infer, or invent any details. If the input is empty, nonsensical, or unrelated to real estate, respond exactly with:\n\nNo content to summarize.\n\nIf the thread is long, condense into the most critical 2–3 sentences. Output only the summary—no headers, no extra commentary.",
            "hyperparameters": {
                "max_tokens": 256,
                "temperature": 0.3,
                "top_p": 1.0,
                "top_k": 0,
                "repetition_penalty": 1.1
            }
        },

        "intro_email": {
            "system": f"""You are a realtor responding to a potential client. {realtor_bio}Write a brief, professional realtor introductory email{tone}{style}{sample_instruction}.

Keep it simple: greeting, brief enthusiasm, ask 1-2 basic questions about their needs, and close naturally.

Do NOT invent specific market data, property details, or services not mentioned.
Do NOT include email signatures, formal closings, or sign-offs like "Best regards," "Sincerely," or "[Your Name]".

Example:
Hello John, I'm excited to help you with your home search! What type of property are you looking for, and do you have a preferred area in mind? Have you started the pre-approval process yet? I'd be happy to help guide you through the next steps.""",

            "hyperparameters": {
                "max_tokens": 100,
                "temperature": 0.3,
                "top_p": 0.8,
                "top_k": 50,
                "repetition_penalty": 1.0
            }
        },

        "continuation_email": {
            "system": f"""You are a realtor continuing an email conversation. {realtor_bio}Respond{tone}{style}{sample_instruction}.

Respond naturally to their message, ask 1-2 relevant follow-up questions to understand their needs better, and suggest a helpful next step.

Do NOT invent specific properties, market data, or services not mentioned. Keep responses conversational and focused on learning more about their situation.
Do NOT include email signatures, formal closings, or sign-offs like "Best regards," "Sincerely," or "[Your Name]".

Example:
Thanks for sharing those details! It sounds like you're looking for something specific. What's most important to you in terms of location - are you focused on a particular school district or commute? Have you had a chance to get pre-approved so we know what price range to focus on?""",

            "hyperparameters": {
                "max_tokens": 150,
                "temperature": 0.3,
                "top_p": 0.8,
                "top_k": 50,
                "repetition_penalty": 1.0
            }
        },

        "closing_referral": {
            "system": f"""You are wrapping up a conversation as the realtor, either because the client is ready for direct contact or needs to be referred elsewhere. {realtor_bio}Write your response{tone}{style}{sample_instruction}.

CRITICAL REQUIREMENTS:
– Output ONLY the email body content
– Maintain your realtor persona and expertise
– Summarize concrete next steps clearly
– Do NOT invent details not previously discussed
– Do NOT include email signatures, formal closings, or sign-offs like "Best regards," "Sincerely," or "[Your Name]"

CLOSING SCENARIOS:
If the lead is QUALIFIED and ready for human interaction:
– Recap their key requirements and timeline
– Confirm next steps (showings, pre-approval, market analysis, etc.)
– Provide clear direction on how to proceed
– Maintain urgency without being pushy

If making a REFERRAL:
– Explain why you're referring them (location, specialty, etc.)
– Provide factual referral information only if already established
– Offer to facilitate the introduction if appropriate

FINAL VALUE ADD:
– Include relevant market insight or timing considerations
– Reinforce your commitment to their success
– Leave the door open for future opportunities if appropriate""",

            "hyperparameters": {
                "max_tokens": 200,
                "temperature": 0.3,
                "top_p": 0.8,
                "top_k": 40,
                "repetition_penalty": 1.0
            }
        },

        "selector_llm": {
            "system": "You are a classifier for real estate email automation. Choose exactly one action: summarizer, intro_email, continuation_email, or closing_referral. Output only that keyword.\n\nRules:\n– intro_email: First contact from a new lead\n– continuation_email: Ongoing conversation that needs more qualification/development\n– closing_referral: Lead is ready for human contact OR needs referral\n– summarizer: Thread is too long and needs condensing before processing\n\nPrioritize continuation_email to maximize information gathering before flagging for human intervention.",
            "hyperparameters": {
                "max_tokens": 2,
                "temperature": 0.0,
                "top_p": 1.0,
                "top_k": 1,
                "repetition_penalty": 1.0
            }
        },

        "reviewer_llm": {
            "system": """You are a business intelligence reviewer determining when a real estate conversation requires the realtor's personal attention. Output exactly one keyword: FLAG or CONTINUE.

FLAG only when the conversation contains issues that require the realtor's direct expertise or intervention:

BUSINESS LOGIC FLAGS:
1. Pricing discussions, negotiations, or offer-related conversations
2. Complex market analysis requests or competitive property comparisons  
3. Scheduling conflicts, urgent timing issues, or time-sensitive opportunities
4. Client expressing dissatisfaction, confusion, or service concerns
5. Legal/contractual questions beyond basic information (HOA bylaws, deed restrictions, etc.)
6. Financing complications or unique lending situations
7. Referral requests or partnership/vendor discussions
8. The AI appears to have given potentially incorrect market information
9. Conversation is going in circles or AI seems unable to progress the lead
10. Client requesting direct contact or phone calls
11. Complex property conditions, inspections, or repair negotiations
12. Investment property analysis or rental/commercial discussions

CONTINUE for typical conversations like:
- Initial inquiries about buying/selling homes
- Basic qualification questions (budget, timeline, preferences)
- General market information and property type discussions
- Standard showing requests and availability coordination
- Routine follow-up communications
- Educational content about the buying/selling process

Remember: The goal is identifying when the REALTOR'S specific expertise is needed, not content safety.""",
            "hyperparameters": {
                "max_tokens": 1,
                "temperature": 0.0,
                "top_p": 1.0,
                "top_k": 1,
                "repetition_penalty": 1.0
            }
        }
    }

# Backwards compatibility - provide the original PROMPTS variable for scenarios that don't need preferences
PROMPTS = get_prompts()