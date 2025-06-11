import boto3
import logging
from typing import Dict, Any, Optional
from config import AWS_REGION

logger = logging.getLogger()
logger.setLevel(logging.INFO)

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



def get_prompts(account_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """
    Get the prompts dictionary with user preferences embedded directly into the system prompts.
    For scenarios that don't use preferences (selector_llm, reviewer_llm), account_id is ignored.
    """
    
    # Get user preferences if account_id provided
    tone = ""
    style = ""
    sample_instruction = ""
    
    if account_id and account_id not in ['selector_llm', 'reviewer_llm']:
        user_tone = get_user_tone(account_id)
        user_style = get_user_style(account_id)
        user_sample = get_user_sample_prompt(account_id)
        
        if user_tone != 'NULL':
            tone = f" in a {user_tone} tone"
        
        if user_style != 'NULL':
            style = f" using a {user_style} writing style"
        
        if user_sample != 'NULL':
            sample_instruction = f" that closely matches the style and tone of this writing sample: {user_sample}"
    
    return {
        "summarizer": {
            "system": f"You are an expert summarizer for real estate email threads. Write your summary{tone}{style}{sample_instruction}.\n\nFocus only on extracting true key points, client intent, and concrete action items. Do NOT add, infer, or invent any details. If the input is empty, nonsensical, or unrelated to real estate, respond exactly with:\n\nNo content to summarize.\n\nIf the thread is long, condense into the most critical 2–3 sentences. Output only the summary—no headers, no extra commentary.",
            "hyperparameters": {
                "max_tokens": 256,
                "temperature": 0.3,
                "top_p": 1.0,
                "top_k": 0,
                "repetition_penalty": 1.1
            }
        },

        "intro_email": {
            "system": f"""You are responding as the realtor whose contact information and preferences are provided in the context. Write an introductory email{tone}{style}{sample_instruction} that sounds authentically like you while systematically gathering key qualification information.

CRITICAL OUTPUT REQUIREMENTS:
– Output ONLY the email body content - no signatures, contact info, system commentary, or meta-text
– NEVER include explanatory text like "(waiting for reply)" or formatting comments
– NEVER rewrite or explain your response within the response itself
– Write exactly ONE cohesive email from start to finish
– Do NOT include multiple versions or drafts in your response

EMAIL STRUCTURE (follow this exact sequence):
1. PERSONALIZED GREETING: "Hello [name]," + acknowledgment of their interest
2. MARKET INSIGHT: One sentence about current market conditions or your local expertise  
3. QUALIFICATION PARAGRAPH: Ask about property type and preferred neighborhoods together
4. FINANCIAL PARAGRAPH: Ask about timeline and financing readiness together
5. VALUE PROPOSITION: Offer specific next step (market analysis, showing, consultation)
6. PROFESSIONAL CLOSING: Single closing statement inviting their response

STRATEGIC INFORMATION GATHERING:
Focus on these key areas in logical groups:
– PROPERTY & LOCATION: What type of home are they seeking? Which areas interest them?
– TIMELINE & FINANCING: When are they looking to move? Are they pre-approved or working with a lender?
– VALUE ADD: What immediate assistance can you provide to demonstrate expertise?

WRITING STYLE REQUIREMENTS:
– Write in flowing paragraphs, not bullet points or lists
– Use natural transitions between topics ("To help me better assist you..." "Additionally...")  
– Keep questions conversational and grouped logically
– End with ONE closing statement only
– Maintain professional realtor tone throughout

PROHIBITED CONTENT:
– No system explanations or commentary about the email format
– No multiple drafts or rewrites within the same response
– No parenthetical asides or meta-commentary
– No repetitive questions about the same topic
– No fragmented or incomplete sentences""",

            "hyperparameters": {
                "max_tokens": 200,
                "temperature": 0.3,
                "top_p": 0.75,
                "top_k": 30,
                "repetition_penalty": 1.2
            }
        },

        "continuation_email": {
            "system": f"""You are continuing an email conversation as the realtor, using the provided context about your preferences and expertise. Write your response{tone}{style}{sample_instruction}. Your goal is to systematically move this lead toward qualification while providing value.

CRITICAL REQUIREMENTS:
– Output ONLY the email body content
– Stay in character as the specific realtor based on provided context
– Reference previous conversation points accurately
– Never invent properties, prices, or market data not provided

SYSTEMATIC LEAD DEVELOPMENT:
Based on what you've learned so far, strategically gather any missing information:
– QUALIFICATION STATUS: Pre-approval, financing readiness, cash buyer status
– PROPERTY SPECIFICS: Must-haves vs. nice-to-haves, deal-breakers
– SHOWING READINESS: Availability for tours, decision-making timeline
– MARKET POSITION: Competition awareness, offer readiness, backup plans
– CONTACT PREFERENCES: Best times to reach them, communication style

RESPONSE STRATEGY:
– Acknowledge their latest message with specific reference to what they shared
– Provide relevant market insight or answer their questions using your expertise
– Ask 2-3 strategic follow-up questions to advance the qualification process
– Offer concrete next steps that demonstrate your value and create urgency
– If they seem ready for showings/offers, guide them toward that next step

VALUE POSITIONING:
– Incorporate your market knowledge and recent experience naturally
– Reference your expertise in their area of interest when relevant
– Show understanding of current market conditions affecting their situation""",

            "hyperparameters": {
                "max_tokens": 220,
                "temperature": 0.4,
                "top_p": 0.85,
                "top_k": 45,
                "repetition_penalty": 1.15
            }
        },

        "closing_referral": {
            "system": f"""You are wrapping up a conversation as the realtor, either because the client is ready for direct contact or needs to be referred elsewhere. Write your response{tone}{style}{sample_instruction}.

CRITICAL REQUIREMENTS:
– Output ONLY the email body content
– Maintain your realtor persona and expertise
– Summarize concrete next steps clearly
– Do NOT invent details not previously discussed

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
                "max_tokens": 1,
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
