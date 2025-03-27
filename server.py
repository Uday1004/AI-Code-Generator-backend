from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
from dotenv import load_dotenv
import os

load_dotenv()
 
API_key = os.getenv("API_KEY")
if API_key is None:
    raise ValueError("API_KEY is not set in .env file")


app = FastAPI()

genai.configure(api_key=API_key)  # Replace with your actual API key
model = genai.GenerativeModel("gemini-1.5-pro")

class PromptRequest(BaseModel):from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import google.generativeai as genai
from dotenv import load_dotenv
import os

load_dotenv()

API_key = os.getenv("API_KEY")
if API_key is None:
    raise ValueError("API_KEY is not set in .env file")


app = FastAPI()

# Configure CORS with more specific origins if possible
origins = [
    "http://localhost",
    "https://ai-code-generator-tau.vercel.app/",  # Add your frontend's port here if different
    # Add other origins as needed, e.g., your deployed frontend URL
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # Use the defined origins list
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

genai.configure(api_key=API_key)
model = genai.GenerativeModel("gemini-1.5-pro")

class PromptRequest(BaseModel):
    prompt: str

# Define a global system context
SYSTEM_CONTEXT = (
    "You are a code generator specialized in React.js you have to code always in React.js. The user always wants code in React.js. The code should be in a single component, not multiple examples. Do not include any explanations, comments, or additional details—just provide the code in a single functional component. If the user asks for styling, prefer inline styles where possible; otherwise, use constants within the component. If the user requests a library, just import it and implement it directly in the component. Follow the format below without deviation:"
)

@app.post("/prompt")
async def get_prompt(prompt_request: PromptRequest):
    # Prepend the system context to the user’s prompt
    final_prompt = f"{SYSTEM_CONTEXT}\n\n{prompt_request.prompt}"

    response = model.generate_content(final_prompt)

    if not response.candidates or not response.candidates[0].content.parts:
        return {"error": "No valid response generated. Modify your prompt."}

    generated_code = response.text
    return {"code": generated_code}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

    prompt: str

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Define a global system context
SYSTEM_CONTEXT = (
    "You are a code generator specialized in React.js you have to code always in React.js. The user always wants code in React.js. The code should be in a single component, not multiple examples. Do not include any explanations, comments, or additional details—just provide the code in a single functional component. If the user asks for styling, prefer inline styles where possible; otherwise, use constants within the component. If the user requests a library, just import it and implement it directly in the component. Follow the format below without deviation:"
)

@app.post("/prompt")
async def get_prompt(prompt_request: PromptRequest):
    # Prepend the system context to the user’s prompt
    final_prompt = f"{SYSTEM_CONTEXT}\n\n{prompt_request.prompt}"

    response = model.generate_content(final_prompt)

    if not response.candidates or not response.candidates[0].content.parts:
        return {"error": "No valid response generated. Modify your prompt."}

    generated_code = response.text
    return {"code": generated_code}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
