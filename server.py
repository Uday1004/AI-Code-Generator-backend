from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
from dotenv import load_dotenv
import os
import uvicorn

# Load environment variables
load_dotenv()
API_key = os.getenv("API_KEY")

if not API_key:
    raise ValueError("API_KEY is not set in .env file")

# Initialize FastAPI app
app = FastAPI()

# Configure CORS
origins = [
    "http://localhost:5173/",
    "https://ai-code-generator-tau.vercel.app/",  # Add your frontend URL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure GenAI
genai.configure(api_key=API_key)
model = genai.GenerativeModel("gemini-1.5-pro")

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
    response = model.generate_content(final_prompt)

    if not response.candidates or not response.candidates[0].content.parts:
        return {"error": "No valid response generated. Modify your prompt."}

    return {"code": response.text}

# Run the app
if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
