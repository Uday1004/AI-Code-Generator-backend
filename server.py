from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
from dotenv import load_dotenv
import os
import uvicorn

# Load environment variables
load_dotenv()
API_key = os.getenv("API_KEY")
if API_key:
    API_key = API_key.strip().strip('"').strip("'")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip().strip('"').strip("'")

if not API_key:
    raise ValueError("API_KEY is not set in .env file")

# Initialize FastAPI app
app = FastAPI()

# Configure CORS
 

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://ai-code-generator-tau.vercel.app"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure GenAI
genai.configure(api_key=API_key)
model = genai.GenerativeModel(MODEL_NAME)

# Pydantic model for request
class PromptRequest(BaseModel):
    prompt: str

# System context for prompt generation
SYSTEM_CONTEXT = (
    "You are a strict code generator for React.js. Always generate code in a single functional component—"
    "no multiple examples, no explanations, no comments. Use inline styles where possible; otherwise, define styles "
    "as constants within the component. If a library is requested, import and implement it directly. Always use "
    "Bootstrap with its components for UI tasks. If a specific library or design is mentioned, follow it exactly. No deviations."
)

@app.post("/prompt")
async def get_prompt(prompt_request: PromptRequest):
    final_prompt = f"{SYSTEM_CONTEXT}\n\n{prompt_request.prompt}"
    try:
        response = model.generate_content(final_prompt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gemini request failed: {exc}")

    if not response.candidates or not response.candidates[0].content.parts:
        return {"error": "No valid response generated. Modify your prompt."}

    return {"code": response.text}

# Run the app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8010)
